"""Marketplace mixed-auth fix: the four remaining end-user marketplace
routes in app/api/v1/marketplace.py that took a client-supplied identity
field and trusted it outright:

  * POST /marketplace/templates                    (publish_template)
  * POST /marketplace/connect/onboarding            (start_connect_onboarding)
  * POST /marketplace/templates/{slug}/checkout     (create_checkout)
  * POST /marketplace/licenses/{license_id}/apply   (apply_template_license)

Each previously accepted creator_id/buyer_id in the request body and used
it directly for both the "who is acting" identity and (in three of the
four) the ownership/authorization check itself — so any caller could
supply someone else's id and publish templates as them, start a Stripe
Connect account in their name, buy a template project isn't theirs, or
apply a license they never purchased.

This file is scoped ONLY to these four routes. GET /marketplace/creators/
{creator_id}/dashboard is already fixed (Batch 2B, see
test_marketplace_dashboard.py) and is not touched here. POST /marketplace/
stripe/webhook is unrelated (server-to-server, verified via
stripe.Webhook.construct_event, not a caller-identity route) and is not
touched here either.

Identity (get_current_user) and every ownership/business check run for
real against the test database, never mocked, matching every other
Batch 1/2A/2B/M1/M2/priority-fix security test file in this program.

The only genuine external boundary these routes cross is the Stripe SDK,
reached only from start_connect_onboarding (create_connect_onboarding) and
create_checkout (create_template_payment_intent). Both are monkeypatched
on the loaded marketplace module — the external SDK boundary — because the
`stripe` package itself is not installed in this CI test slice (see
app/services/marketplace_payments.py: `stripe` is only ever imported
inside a function body, never at module level, so marketplace.py loads
fine without it). Nothing else is mocked.
"""
from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db.session import get_db
from app.models.entities import (
    CreatorConnectAccount, MarketplaceTemplate, MarketplaceTemplateStatus, Project,
    Template, TemplateLicense, TemplateLicenseStatus, Timeline, User,
)

API_V1 = "/api/v1"


def _load_marketplace_router():
    """Load app/api/v1/marketplace.py directly, bypassing app.api.__init__
    — same reasoning and same direct-load pattern as
    test_marketplace_dashboard.py and every other Batch 1/2A/2B/M1/M2 test
    file. A fresh module object (distinct from test_marketplace_dashboard's)
    so monkeypatching create_connect_onboarding/create_template_payment_intent
    here can never leak into that file's tests."""
    module_path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "marketplace.py"
    spec = importlib.util.spec_from_file_location("_vantacut_test_marketplace_mixed_router", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_marketplace_module = _load_marketplace_router()


@pytest.fixture()
def app_client(db_session):
    app = FastAPI()
    app.include_router(_marketplace_module.router, prefix=API_V1)

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def stripe_boundary(monkeypatch):
    """Monkeypatch the two functions that are marketplace.py's only calls
    into the external Stripe SDK boundary. Each call is recorded so tests
    can assert zero Stripe side effects on unauthorized requests."""
    calls = {"connect_onboarding": [], "payment_intent": []}

    def _fake_connect_onboarding(*, creator_email, idempotency_key):
        calls["connect_onboarding"].append((creator_email, idempotency_key))
        return {
            "account_id": f"acct_{uuid.uuid4().hex[:16]}",
            "onboarding_url": "https://connect.stripe.com/fake-onboarding",
            "account": {"details_submitted": False, "charges_enabled": False, "payouts_enabled": False, "requirements": {}},
        }

    def _fake_payment_intent(*, license_id, amount_cents, currency, transfer_group):
        calls["payment_intent"].append((license_id, amount_cents, currency, transfer_group))
        return {
            "payment_intent_id": f"pi_{uuid.uuid4().hex[:16]}",
            "client_secret": f"pi_{uuid.uuid4().hex[:16]}_secret_fake",
            "status": "requires_payment_method",
        }

    monkeypatch.setattr(_marketplace_module, "create_connect_onboarding", _fake_connect_onboarding)
    monkeypatch.setattr(_marketplace_module, "create_template_payment_intent", _fake_payment_intent)
    return calls


def _make_user(db_session) -> User:
    user = User(email=f"mkt-mixed-{uuid.uuid4().hex[:12]}@example.com", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


def _auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _make_project(db_session, owner: User) -> Project:
    project = Project(owner_id=owner.id, name="Marketplace mixed-identity test project")
    db_session.add(project)
    db_session.flush()
    return project


def _make_template(db_session, project: Project) -> Template:
    template = Template(project_id=project.id, name="Marketplace mixed-identity test template", structure_json={})
    db_session.add(template)
    db_session.flush()
    return template


def _make_listing(db_session, creator: User, project: Project | None = None, price_cents: int = 1000) -> MarketplaceTemplate:
    project = project or _make_project(db_session, creator)
    template = _make_template(db_session, project)
    listing = MarketplaceTemplate(
        template_id=template.id, creator_id=creator.id, slug=f"mkt-mixed-{uuid.uuid4().hex[:12]}",
        title="Marketplace mixed-identity test listing", status=MarketplaceTemplateStatus.PUBLISHED.value,
        price_cents=price_cents, currency="usd", encrypted_payload="not-a-real-payload",
        payload_sha256="0" * 64, safe_preview_json={},
    )
    db_session.add(listing)
    db_session.flush()
    return listing


def _make_timeline(db_session, project: Project) -> Timeline:
    timeline = Timeline(project_id=project.id, name="Marketplace mixed-identity test timeline", settings_json={})
    db_session.add(timeline)
    db_session.flush()
    return timeline


def _make_license(
    db_session, listing: MarketplaceTemplate, buyer: User, project: Project,
    status: str = TemplateLicenseStatus.PAYMENT_SUCCEEDED.value,
) -> TemplateLicense:
    license_row = TemplateLicense(
        marketplace_template_id=listing.id, buyer_id=buyer.id, project_id=project.id,
        status=status, gross_amount_cents=listing.price_cents, currency=listing.currency,
        creator_share_cents=800, platform_share_cents=200,
        transfer_group=f"tmpl_license_{uuid.uuid4().hex}", template_payload_sha256=listing.payload_sha256,
        blackbox_render_only=True,
    )
    db_session.add(license_row)
    db_session.flush()
    return license_row


# ---------------------------------------------------------------------------
# publish_template — POST /marketplace/templates
# ---------------------------------------------------------------------------

def _publish_body(template_id, **overrides) -> dict:
    body = {
        "template_id": str(template_id), "slug": f"pub-{uuid.uuid4().hex[:10]}", "title": "A published template",
        "price_cents": 500, "currency": "usd", "safe_preview": {}, "private_payload": {"lut": "opaque"},
    }
    body.update(overrides)
    return body


def test_publish_anonymous_rejected(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    template = _make_template(db_session, project)

    response = app_client.post(f"{API_V1}/marketplace/templates", json=_publish_body(template.id))
    assert response.status_code == 401
    assert db_session.query(MarketplaceTemplate).filter_by(template_id=template.id).first() is None


def test_publish_invalid_token_rejected(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    template = _make_template(db_session, project)

    response = app_client.post(
        f"{API_V1}/marketplace/templates", json=_publish_body(template.id),
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401
    assert db_session.query(MarketplaceTemplate).filter_by(template_id=template.id).first() is None


def test_publish_non_owner_rejected_zero_db_mutation(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    template = _make_template(db_session, project)
    stranger = _make_user(db_session)

    response = app_client.post(
        f"{API_V1}/marketplace/templates", json=_publish_body(template.id), headers=_auth(stranger),
    )
    assert response.status_code == 403
    assert db_session.query(MarketplaceTemplate).filter_by(template_id=template.id).first() is None


def test_publish_spoofed_legacy_creator_id_has_no_effect(app_client, db_session):
    """The schema no longer declares a creator_id field at all — even if an
    attacker's request body includes one (Pydantic silently drops unknown
    fields), the publishing creator is still the caller's own id."""
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    template = _make_template(db_session, project)
    attacker = _make_user(db_session)

    response = app_client.post(
        f"{API_V1}/marketplace/templates", json=_publish_body(template.id, creator_id=str(owner.id)),
        headers=_auth(attacker),
    )
    assert response.status_code == 403
    assert db_session.query(MarketplaceTemplate).filter_by(template_id=template.id).first() is None


def test_publish_rightful_owner_succeeds(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    template = _make_template(db_session, project)

    response = app_client.post(
        f"{API_V1}/marketplace/templates", json=_publish_body(template.id), headers=_auth(owner),
    )
    assert response.status_code == 201, response.text
    listing = db_session.query(MarketplaceTemplate).filter_by(template_id=template.id).first()
    assert listing is not None
    assert listing.creator_id == owner.id


# ---------------------------------------------------------------------------
# start_connect_onboarding — POST /marketplace/connect/onboarding
# ---------------------------------------------------------------------------

def test_connect_onboarding_anonymous_rejected(app_client, db_session, stripe_boundary):
    response = app_client.post(f"{API_V1}/marketplace/connect/onboarding")
    assert response.status_code == 401
    assert stripe_boundary["connect_onboarding"] == []


def test_connect_onboarding_invalid_token_rejected(app_client, db_session, stripe_boundary):
    response = app_client.post(
        f"{API_V1}/marketplace/connect/onboarding", headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401
    assert stripe_boundary["connect_onboarding"] == []


def test_connect_onboarding_spoofed_legacy_creator_id_has_no_effect(app_client, db_session, stripe_boundary):
    """The route no longer accepts a request body at all (its sole field,
    creator_id, was removed) — a spoofed creator_id in an extra JSON body
    is simply ignored, and the account is created for the authenticated
    caller, never the id in the body."""
    caller = _make_user(db_session)
    victim = _make_user(db_session)

    response = app_client.post(
        f"{API_V1}/marketplace/connect/onboarding", json={"creator_id": str(victim.id)}, headers=_auth(caller),
    )
    assert response.status_code == 200, response.text
    assert len(stripe_boundary["connect_onboarding"]) == 1
    assert stripe_boundary["connect_onboarding"][0][0] == caller.email
    account = db_session.query(CreatorConnectAccount).filter_by(creator_id=caller.id).first()
    assert account is not None
    victim_account = db_session.query(CreatorConnectAccount).filter_by(creator_id=victim.id).first()
    assert victim_account is None


def test_connect_onboarding_rightful_user_succeeds(app_client, db_session, stripe_boundary):
    caller = _make_user(db_session)

    response = app_client.post(f"{API_V1}/marketplace/connect/onboarding", headers=_auth(caller))
    assert response.status_code == 200, response.text
    assert len(stripe_boundary["connect_onboarding"]) == 1
    account = db_session.query(CreatorConnectAccount).filter_by(creator_id=caller.id).first()
    assert account is not None


def test_connect_onboarding_already_connected_rejected_zero_new_stripe_call(app_client, db_session, stripe_boundary):
    caller = _make_user(db_session)
    db_session.add(CreatorConnectAccount(
        creator_id=caller.id, stripe_account_id=f"acct_{uuid.uuid4().hex[:16]}",
        details_submitted=True, charges_enabled=True, payouts_enabled=True,
    ))
    db_session.flush()

    response = app_client.post(f"{API_V1}/marketplace/connect/onboarding", headers=_auth(caller))
    assert response.status_code == 409
    assert stripe_boundary["connect_onboarding"] == []


# ---------------------------------------------------------------------------
# create_checkout — POST /marketplace/templates/{slug}/checkout
# ---------------------------------------------------------------------------

def test_checkout_anonymous_rejected(app_client, db_session, stripe_boundary):
    creator = _make_user(db_session)
    listing = _make_listing(db_session, creator)
    buyer = _make_user(db_session)
    buyer_project = _make_project(db_session, buyer)

    response = app_client.post(
        f"{API_V1}/marketplace/templates/{listing.slug}/checkout", json={"project_id": str(buyer_project.id)},
    )
    assert response.status_code == 401
    assert stripe_boundary["payment_intent"] == []
    assert db_session.query(TemplateLicense).filter_by(marketplace_template_id=listing.id).first() is None


def test_checkout_invalid_token_rejected(app_client, db_session, stripe_boundary):
    creator = _make_user(db_session)
    listing = _make_listing(db_session, creator)
    buyer = _make_user(db_session)
    buyer_project = _make_project(db_session, buyer)

    response = app_client.post(
        f"{API_V1}/marketplace/templates/{listing.slug}/checkout", json={"project_id": str(buyer_project.id)},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401
    assert stripe_boundary["payment_intent"] == []


def test_checkout_non_owner_of_project_rejected_zero_side_effects(app_client, db_session, stripe_boundary):
    creator = _make_user(db_session)
    listing = _make_listing(db_session, creator)
    real_owner = _make_user(db_session)
    project = _make_project(db_session, real_owner)
    stranger = _make_user(db_session)

    response = app_client.post(
        f"{API_V1}/marketplace/templates/{listing.slug}/checkout", json={"project_id": str(project.id)},
        headers=_auth(stranger),
    )
    assert response.status_code == 403
    assert stripe_boundary["payment_intent"] == []
    assert db_session.query(TemplateLicense).filter_by(marketplace_template_id=listing.id).first() is None


def test_checkout_spoofed_legacy_buyer_id_has_no_effect(app_client, db_session, stripe_boundary):
    creator = _make_user(db_session)
    listing = _make_listing(db_session, creator)
    real_owner = _make_user(db_session)
    project = _make_project(db_session, real_owner)
    attacker = _make_user(db_session)

    response = app_client.post(
        f"{API_V1}/marketplace/templates/{listing.slug}/checkout",
        json={"project_id": str(project.id), "buyer_id": str(real_owner.id)},
        headers=_auth(attacker),
    )
    assert response.status_code == 403
    assert stripe_boundary["payment_intent"] == []
    assert db_session.query(TemplateLicense).filter_by(marketplace_template_id=listing.id).first() is None


def test_checkout_creator_cannot_purchase_own_template(app_client, db_session, stripe_boundary):
    creator = _make_user(db_session)
    listing = _make_listing(db_session, creator)
    own_project = _make_project(db_session, creator)

    response = app_client.post(
        f"{API_V1}/marketplace/templates/{listing.slug}/checkout", json={"project_id": str(own_project.id)},
        headers=_auth(creator),
    )
    assert response.status_code == 400
    assert stripe_boundary["payment_intent"] == []


def test_checkout_rightful_buyer_succeeds(app_client, db_session, stripe_boundary):
    creator = _make_user(db_session)
    listing = _make_listing(db_session, creator)
    buyer = _make_user(db_session)
    buyer_project = _make_project(db_session, buyer)

    response = app_client.post(
        f"{API_V1}/marketplace/templates/{listing.slug}/checkout", json={"project_id": str(buyer_project.id)},
        headers=_auth(buyer),
    )
    assert response.status_code == 201, response.text
    assert len(stripe_boundary["payment_intent"]) == 1
    license_row = db_session.query(TemplateLicense).filter_by(marketplace_template_id=listing.id).first()
    assert license_row is not None
    assert license_row.buyer_id == buyer.id


# ---------------------------------------------------------------------------
# apply_template_license — POST /marketplace/licenses/{license_id}/apply
# ---------------------------------------------------------------------------

def _apply_body(timeline_id, **overrides) -> dict:
    body = {"timeline_id": str(timeline_id)}
    body.update(overrides)
    return body


def test_apply_anonymous_rejected(app_client, db_session):
    creator = _make_user(db_session)
    listing = _make_listing(db_session, creator)
    buyer = _make_user(db_session)
    project = _make_project(db_session, buyer)
    timeline = _make_timeline(db_session, project)
    license_row = _make_license(db_session, listing, buyer, project)

    response = app_client.post(
        f"{API_V1}/marketplace/licenses/{license_row.id}/apply", json=_apply_body(timeline.id),
    )
    assert response.status_code == 401
    db_session.refresh(license_row)
    assert license_row.status == TemplateLicenseStatus.PAYMENT_SUCCEEDED.value


def test_apply_invalid_token_rejected(app_client, db_session):
    creator = _make_user(db_session)
    listing = _make_listing(db_session, creator)
    buyer = _make_user(db_session)
    project = _make_project(db_session, buyer)
    timeline = _make_timeline(db_session, project)
    license_row = _make_license(db_session, listing, buyer, project)

    response = app_client.post(
        f"{API_V1}/marketplace/licenses/{license_row.id}/apply", json=_apply_body(timeline.id),
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


def test_apply_non_buyer_rejected_non_enumerating(app_client, db_session):
    creator = _make_user(db_session)
    listing = _make_listing(db_session, creator)
    buyer = _make_user(db_session)
    project = _make_project(db_session, buyer)
    timeline = _make_timeline(db_session, project)
    license_row = _make_license(db_session, listing, buyer, project)
    stranger = _make_user(db_session)

    response = app_client.post(
        f"{API_V1}/marketplace/licenses/{license_row.id}/apply", json=_apply_body(timeline.id),
        headers=_auth(stranger),
    )
    assert response.status_code == 404
    db_session.refresh(license_row)
    assert license_row.status == TemplateLicenseStatus.PAYMENT_SUCCEEDED.value
    assert license_row.timeline_id is None


def test_apply_spoofed_legacy_buyer_id_has_no_effect(app_client, db_session):
    creator = _make_user(db_session)
    listing = _make_listing(db_session, creator)
    buyer = _make_user(db_session)
    project = _make_project(db_session, buyer)
    timeline = _make_timeline(db_session, project)
    license_row = _make_license(db_session, listing, buyer, project)
    attacker = _make_user(db_session)

    response = app_client.post(
        f"{API_V1}/marketplace/licenses/{license_row.id}/apply",
        json=_apply_body(timeline.id, buyer_id=str(buyer.id)), headers=_auth(attacker),
    )
    assert response.status_code == 404
    db_session.refresh(license_row)
    assert license_row.status == TemplateLicenseStatus.PAYMENT_SUCCEEDED.value


def test_apply_rightful_buyer_succeeds(app_client, db_session):
    creator = _make_user(db_session)
    listing = _make_listing(db_session, creator)
    buyer = _make_user(db_session)
    project = _make_project(db_session, buyer)
    timeline = _make_timeline(db_session, project)
    license_row = _make_license(db_session, listing, buyer, project)

    response = app_client.post(
        f"{API_V1}/marketplace/licenses/{license_row.id}/apply", json=_apply_body(timeline.id), headers=_auth(buyer),
    )
    assert response.status_code == 200, response.text
    db_session.refresh(license_row)
    assert license_row.status == TemplateLicenseStatus.APPLIED.value
    assert license_row.timeline_id == timeline.id
