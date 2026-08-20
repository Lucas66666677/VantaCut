from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.entities import (
    CreatorConnectAccount, MarketplaceTemplate, MarketplaceTemplateStatus, Project,
    Template, TemplateLicense, TemplateLicenseStatus, Timeline, User,
)
from app.schemas.marketplace import (
    ApplyLicenseRequest, ApplyLicenseResponse, CheckoutResponse,
    ConnectOnboardingResponse, CreatorDashboardResponse, CreateLicenseRequest, DashboardPersona,
    MarketplaceTemplateResponse, PublishMarketplaceTemplateRequest,
)
from app.services.marketplace_payments import (
    MarketplacePaymentError, create_connect_onboarding, create_template_payment_intent, verify_webhook,
)
from app.services.marketplace_security import MarketplaceSecurityError, encrypt_template_payload
from app.services.marketplace_settlement import mark_payment_failed, record_payment_succeeded


router = APIRouter(prefix="/marketplace", tags=["marketplace"])


def _public_template(row: MarketplaceTemplate) -> MarketplaceTemplateResponse:
    return MarketplaceTemplateResponse(
        id=row.id, template_id=row.template_id, slug=row.slug, title=row.title, summary=row.summary,
        price_cents=row.price_cents, currency=row.currency, status=row.status, safe_preview=row.safe_preview_json,
    )


def _split_price(gross: int) -> tuple[int, int]:
    if settings.marketplace_creator_share_bps + settings.marketplace_platform_share_bps != 10_000:
        raise MarketplacePaymentError("Marketplace revenue-share basis points must sum to 10000")
    creator = gross * settings.marketplace_creator_share_bps // 10_000
    return creator, gross - creator


@router.post("/templates", response_model=MarketplaceTemplateResponse, status_code=status.HTTP_201_CREATED)
def publish_template(
    payload: PublishMarketplaceTemplateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MarketplaceTemplateResponse:
    template = db.get(Template, payload.template_id)
    if template is None or template.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the source template owner can publish it")
    if db.scalar(select(MarketplaceTemplate.id).where(MarketplaceTemplate.template_id == template.id)):
        raise HTTPException(status_code=409, detail="Template already has a marketplace listing")
    try:
        encrypted_payload, digest = encrypt_template_payload(payload.private_payload)
    except MarketplaceSecurityError as exc:
        raise HTTPException(status_code=503, detail="Marketplace encryption is unavailable") from exc
    listing = MarketplaceTemplate(
        template_id=template.id, creator_id=current_user.id, slug=payload.slug, title=payload.title,
        summary=payload.summary, status=MarketplaceTemplateStatus.PUBLISHED.value,
        price_cents=payload.price_cents, currency=payload.currency.lower(), encrypted_payload=encrypted_payload,
        encryption_key_version=settings.marketplace_template_key_version, payload_sha256=digest,
        safe_preview_json=payload.safe_preview,
    )
    db.add(listing)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Marketplace template slug already exists") from exc
    db.refresh(listing)
    return _public_template(listing)


@router.post("/connect/onboarding", response_model=ConnectOnboardingResponse)
def start_connect_onboarding(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConnectOnboardingResponse:
    # Caller identity comes exclusively from the verified bearer token now —
    # there is no longer a client-supplied creator_id to look up or trust,
    # so the redundant db.get(User, ...)/404 branch is gone: current_user is
    # already guaranteed to be a real, active user.
    existing = db.scalar(select(CreatorConnectAccount).where(CreatorConnectAccount.creator_id == current_user.id))
    if existing:
        raise HTTPException(status_code=409, detail="Creator already has a Stripe Connect account")
    try:
        result = create_connect_onboarding(
            creator_email=current_user.email, idempotency_key=f"creator:{current_user.id}:connect"
        )
    except MarketplacePaymentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    account = result["account"]
    db.add(CreatorConnectAccount(
        creator_id=current_user.id, stripe_account_id=result["account_id"],
        details_submitted=bool(account.get("details_submitted", False)),
        charges_enabled=bool(account.get("charges_enabled", False)),
        payouts_enabled=bool(account.get("payouts_enabled", False)),
        status_json={"requirements": dict(account.get("requirements") or {})},
    ))
    db.commit()
    return ConnectOnboardingResponse(stripe_account_id=result["account_id"], onboarding_url=result["onboarding_url"])


@router.post("/templates/{slug}/checkout", response_model=CheckoutResponse, status_code=status.HTTP_201_CREATED)
def create_checkout(
    slug: str,
    payload: CreateLicenseRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CheckoutResponse:
    listing = db.scalar(select(MarketplaceTemplate).where(
        MarketplaceTemplate.slug == slug, MarketplaceTemplate.status == MarketplaceTemplateStatus.PUBLISHED.value
    ))
    project = db.get(Project, payload.project_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="Marketplace template not found")
    if project is None or project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Buyer cannot apply a template to this project")
    if listing.creator_id == current_user.id:
        raise HTTPException(status_code=400, detail="Creators cannot purchase their own template")
    creator_share, platform_share = _split_price(listing.price_cents)
    license_row = TemplateLicense(
        marketplace_template_id=listing.id, buyer_id=current_user.id, project_id=project.id,
        gross_amount_cents=listing.price_cents, currency=listing.currency,
        creator_share_cents=creator_share, platform_share_cents=platform_share,
        transfer_group=f"tmpl_license_{uuid4().hex}", template_payload_sha256=listing.payload_sha256,
        blackbox_render_only=True,
    )
    db.add(license_row)
    db.flush()
    try:
        intent = create_template_payment_intent(
            license_id=str(license_row.id), amount_cents=license_row.gross_amount_cents,
            currency=license_row.currency, transfer_group=license_row.transfer_group,
        )
    except MarketplacePaymentError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    license_row.stripe_payment_intent_id = intent["payment_intent_id"]
    db.commit()
    return CheckoutResponse(
        license_id=license_row.id, payment_intent_client_secret=intent["client_secret"],
        amount_cents=license_row.gross_amount_cents, currency=license_row.currency,
    )


@router.post("/licenses/{license_id}/apply", response_model=ApplyLicenseResponse)
def apply_template_license(
    license_id: UUID,
    payload: ApplyLicenseRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplyLicenseResponse:
    license_row = db.scalar(select(TemplateLicense).where(TemplateLicense.id == license_id).with_for_update())
    timeline = db.get(Timeline, payload.timeline_id)
    if license_row is None or license_row.buyer_id != current_user.id:
        raise HTTPException(status_code=404, detail="Template license not found")
    if timeline is None or timeline.project_id != license_row.project_id:
        raise HTTPException(status_code=403, detail="Timeline is not part of this template license project")
    if license_row.status != TemplateLicenseStatus.PAYMENT_SUCCEEDED.value:
        raise HTTPException(status_code=409, detail="The template payment has not succeeded")
    # Store only an opaque reference. No endpoint returns the encrypted Timeline/LUT/Prompt payload.
    timeline.settings_json = {
        **dict(timeline.settings_json or {}),
        "marketplace_blackbox": {
            "license_id": str(license_row.id), "template_id": str(license_row.marketplace_template_id),
            "payload_sha256": license_row.template_payload_sha256, "cloud_render_only": True,
        },
    }
    license_row.timeline_id = timeline.id
    license_row.status = TemplateLicenseStatus.APPLIED.value
    license_row.applied_at = datetime.now(UTC)
    db.commit()
    return ApplyLicenseResponse(license_id=license_row.id, timeline_id=timeline.id, status=license_row.status, blackbox_render_only=True)


@router.post("/stripe/webhook", status_code=status.HTTP_200_OK)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, bool]:
    try:
        event = verify_webhook(await request.body(), request.headers.get("Stripe-Signature"))
    except MarketplacePaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    event_type = event["type"]
    intent = event["data"]["object"]
    if event_type == "account.updated":
        account = db.scalar(select(CreatorConnectAccount).where(
            CreatorConnectAccount.stripe_account_id == str(intent["id"])
        ).with_for_update())
        if account:
            account.details_submitted = bool(intent.get("details_submitted", False))
            account.charges_enabled = bool(intent.get("charges_enabled", False))
            account.payouts_enabled = bool(intent.get("payouts_enabled", False))
            account.status_json = {"requirements": dict(intent.get("requirements") or {})}
            db.commit()
        return {"received": True}
    metadata = dict(intent.get("metadata") or {})
    license_value = metadata.get("template_license_id")
    if not license_value:
        return {"received": True}
    if event_type == "payment_intent.succeeded":
        try:
            record_payment_succeeded(
                db, license_id=UUID(license_value), payment_intent_id=str(intent["id"]),
                charge_id=str(intent.get("latest_charge") or "") or None,
            )
            db.commit()
        except (LookupError, ValueError):
            db.rollback()
            raise HTTPException(status_code=400, detail="Unknown marketplace payment")
    elif event_type == "payment_intent.payment_failed":
        mark_payment_failed(db, payment_intent_id=str(intent["id"]))
        db.commit()
    return {"received": True}


def _creator_dashboard_not_found() -> HTTPException:
    # Same response whether {creator_id} doesn't correspond to a real user or
    # simply isn't the caller's own dashboard — do not confirm to an
    # unauthorized caller whether a given creator_id has marketplace
    # earnings/payout data at all.
    return HTTPException(status_code=404, detail="Creator dashboard not found")


@router.get("/creators/{creator_id}/dashboard", response_model=CreatorDashboardResponse)
def creator_dashboard(
    creator_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreatorDashboardResponse:
    # creator_id is a direct users.id foreign key everywhere in this file
    # (MarketplaceTemplate.creator_id, CreatorConnectAccount.creator_id both
    # FK -> users.id; there is no separate Creator entity) — so "is the
    # caller this creator" is exactly "current_user.id == creator_id".
    # {creator_id} in the path is never trusted as identity on its own: an
    # authenticated caller only ever sees their own dashboard, matching how
    # every other ownership check in this codebase resolves identity from
    # the verified token, not from a client-supplied/path id.
    if creator_id != current_user.id:
        raise _creator_dashboard_not_found()
    templates = db.scalars(select(MarketplaceTemplate).where(MarketplaceTemplate.creator_id == creator_id)).all()
    template_ids = [item.id for item in templates]
    if not template_ids:
        return CreatorDashboardResponse(creator_id=creator_id, template_count=0, successful_uses=0, estimated_mrr_cents=0,
                                        currency=settings.marketplace_currency, top_user_personas=[], payout_status="not_connected")
    successful = TemplateLicenseStatus.FULFILLED.value
    successful_uses = int(db.scalar(select(func.count(TemplateLicense.id)).where(
        TemplateLicense.marketplace_template_id.in_(template_ids), TemplateLicense.status == successful
    )) or 0)
    thirty_days_ago = datetime.now(UTC) - timedelta(days=30)
    estimated_mrr = int(db.scalar(select(func.coalesce(func.sum(TemplateLicense.creator_share_cents), 0)).where(
        TemplateLicense.marketplace_template_id.in_(template_ids), TemplateLicense.status == successful,
        TemplateLicense.fulfilled_at >= thirty_days_ago,
    )) or 0)
    persona_rows = db.execute(
        select(User.subscription_tier, func.count(func.distinct(TemplateLicense.buyer_id)), func.count(TemplateLicense.id))
        .join(TemplateLicense, TemplateLicense.buyer_id == User.id)
        .where(TemplateLicense.marketplace_template_id.in_(template_ids), TemplateLicense.status == successful)
        .group_by(User.subscription_tier)
    ).all()
    personas = []
    for tier, users, uses in persona_rows:
        # k-anonymity threshold: dashboards expose cohorts, never buyer identity or PII.
        if users >= 5:
            personas.append(DashboardPersona(
                segment=f"{tier.value}_creator", users=int(users), usage_count=int(uses),
                share_percent=round(int(uses) * 100 / successful_uses, 1) if successful_uses else 0.0,
            ))
    connect = db.scalar(select(CreatorConnectAccount).where(CreatorConnectAccount.creator_id == creator_id))
    payout_status = "not_connected" if not connect else ("ready" if connect.payouts_enabled else "onboarding_required")
    return CreatorDashboardResponse(
        creator_id=creator_id, template_count=len(templates), successful_uses=successful_uses,
        estimated_mrr_cents=estimated_mrr, currency=templates[0].currency, top_user_personas=personas,
        payout_status=payout_status,
    )
