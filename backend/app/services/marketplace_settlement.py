"""Local ledger projection and delayed Stripe Connect settlement for marketplace exports."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.entities import (
    CreatorConnectAccount,
    MarketplaceLedgerEntry,
    TemplateLicense,
    TemplateLicenseStatus,
)
from app.services.marketplace_payments import MarketplacePaymentError, create_creator_transfer


def _ledger_exists(db: Session, key: str) -> bool:
    return db.scalar(select(MarketplaceLedgerEntry.id).where(MarketplaceLedgerEntry.idempotency_key == key)) is not None


def record_payment_succeeded(
    db: Session, *, license_id: UUID, payment_intent_id: str, charge_id: str | None
) -> TemplateLicense:
    license_row = db.scalar(
        select(TemplateLicense).where(TemplateLicense.id == license_id).with_for_update()
    )
    if license_row is None:
        raise LookupError("Template license not found")
    if license_row.stripe_payment_intent_id != payment_intent_id:
        raise ValueError("Payment intent does not belong to this template license")
    if license_row.status in {TemplateLicenseStatus.PAYMENT_FAILED.value, TemplateLicenseStatus.REVOKED.value}:
        raise ValueError("Template license cannot be paid in its current state")

    license_row.stripe_charge_id = charge_id or license_row.stripe_charge_id
    if license_row.status == TemplateLicenseStatus.CHECKOUT_PENDING.value:
        license_row.status = TemplateLicenseStatus.PAYMENT_SUCCEEDED.value
    events = (
        ("buyer_charge", "credit", license_row.gross_amount_cents, "available"),
        ("creator_payable", "credit", license_row.creator_share_cents, "pending"),
        ("platform_commission", "credit", license_row.platform_share_cents, "pending"),
    )
    for entry_type, direction, amount, status in events:
        key = f"template-license:{license_row.id}:{entry_type}"
        if not _ledger_exists(db, key):
            db.add(MarketplaceLedgerEntry(
                license_id=license_row.id, entry_type=entry_type, direction=direction,
                amount_cents=amount, currency=license_row.currency, status=status,
                stripe_object_id=charge_id, idempotency_key=key,
                metadata_json={"payment_intent_id": payment_intent_id},
            ))
    return license_row


def mark_payment_failed(db: Session, *, payment_intent_id: str) -> None:
    license_row = db.scalar(
        select(TemplateLicense).where(TemplateLicense.stripe_payment_intent_id == payment_intent_id).with_for_update()
    )
    if license_row and license_row.status == TemplateLicenseStatus.CHECKOUT_PENDING.value:
        license_row.status = TemplateLicenseStatus.PAYMENT_FAILED.value


def settle_successful_render(db: Session, *, render_job_id: UUID) -> bool:
    """Transfer the creator share once. Stripe and the local ledger both use stable idempotency keys."""
    license_row = db.scalar(
        select(TemplateLicense)
        .options(joinedload(TemplateLicense.marketplace_template))
        .where(TemplateLicense.render_job_id == render_job_id)
        .with_for_update()
    )
    if license_row is None or license_row.status == TemplateLicenseStatus.FULFILLED.value:
        return False
    if license_row.status != TemplateLicenseStatus.RENDERING.value:
        raise ValueError("Template license is not awaiting render settlement")
    if not license_row.stripe_charge_id:
        raise ValueError("A successful Stripe charge is required before settlement")
    account = db.scalar(
        select(CreatorConnectAccount).where(CreatorConnectAccount.creator_id == license_row.marketplace_template.creator_id)
    )
    if account is None or not account.payouts_enabled:
        raise MarketplacePaymentError("Creator Stripe Connect payouts are not enabled")

    transfer_key = f"template-license:{license_row.id}:creator-transfer"
    if _ledger_exists(db, transfer_key):
        license_row.status = TemplateLicenseStatus.FULFILLED.value
        license_row.fulfilled_at = license_row.fulfilled_at or datetime.now(UTC)
        return False
    transfer_id = create_creator_transfer(
        destination_account=account.stripe_account_id,
        amount_cents=license_row.creator_share_cents,
        currency=license_row.currency,
        source_charge=license_row.stripe_charge_id,
        transfer_group=license_row.transfer_group,
        license_id=str(license_row.id),
    )
    license_row.stripe_transfer_id = transfer_id
    license_row.status = TemplateLicenseStatus.FULFILLED.value
    license_row.fulfilled_at = datetime.now(UTC)
    db.add(MarketplaceLedgerEntry(
        license_id=license_row.id, entry_type="creator_transfer", direction="debit",
        amount_cents=license_row.creator_share_cents, currency=license_row.currency,
        status="paid", stripe_object_id=transfer_id, idempotency_key=transfer_key,
        metadata_json={"render_job_id": str(render_job_id), "destination": account.stripe_account_id},
    ))
    # Do not mutate the original payable row: the debit transfer is its compensating,
    # auditable event and keeps this ledger append-only.
    return True
