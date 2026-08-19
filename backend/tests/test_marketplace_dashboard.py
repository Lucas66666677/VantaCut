"""Batch 2B security tests for GET /marketplace/creators/{creator_id}/dashboard
(app/api/v1/marketplace.py).

This file tests ONLY the dashboard route's new authorization gate. The
rest of marketplace.py (publish_template, checkout, connect onboarding,
apply_template_license, the Stripe webhook) is unchanged in this batch —
those routes still take client-supplied creator_id/buyer_id fields, which
is the broader SPOOFABLE_USER_ID migration, explicitly out of scope here
(see the Batch 2B PR description).

Identity (get_current_user) runs for real against the test database, never
mocked, matching every other Batch 1/2A security test file. No Celery/
Stripe/network boundary is exercised by the dashboard route, so nothing
needs mocking here.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db.session import get_db
from app.models.entities import (
    CreatorConnectAccount, MarketplaceTemplate, MarketplaceTemplateStatus, Project, Template, User,
)


def _load_marketplace_router():
    """Load app/api/v1/marketplace.py directly, bypassing `app.api.__init__`
    — same reasoning as every other Batch 1/2A test file. marketplace.py
    imports app.services.marketplace_security at module level, which needs
    the `cryptography` package (Fernet) to import — added to this CI slice
    specifically for this batch (see backend-auth-tests.yml). It does NOT
    import the `stripe` package at module level (app.services.
    marketplace_payments only does `import stripe` inside a function body),
    so this router loads fine without the stripe SDK installed.
    """
    module_path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "marketplace.py"
    spec = importlib.util.spec_from_file_location("_vantacut_test_marketplace_router", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_marketplace_module = _load_marketplace_router()


@pytest.fixture()
def app_client(db_session):
    app = FastAPI()
    app.include_router(_marketplace_module.router, prefix="/api/v1")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


def _make_user(db_session) -> User:
    user = User(email=f"batch2b-{uuid.uuid4().hex[:12]}@example.com", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


def _make_marketplace_template(db_session, creator: User) -> MarketplaceTemplate:
    project = Project(owner_id=creator.id, name="Batch 2B marketplace test project")
    db_session.add(project)
    db_session.flush()
    template = Template(project_id=project.id, name="Batch 2B test template", structure_json={})
    db_session.add(template)
    db_session.flush()
    listing = MarketplaceTemplate(
        template_id=template.id,
        creator_id=creator.id,
        slug=f"batch2b-{uuid.uuid4().hex[:12]}",
        title="Batch 2B test listing",
        status=MarketplaceTemplateStatus.PUBLISHED.value,
        price_cents=1000,
        currency="usd",
        encrypted_payload="not-a-real-payload",
        payload_sha256="0" * 64,
        safe_preview_json={},
    )
    db_session.add(listing)
    db_session.flush()
    return listing


def test_anonymous_rejected(app_client, db_session):
    creator = _make_user(db_session)
    _make_marketplace_template(db_session, creator)

    response = app_client.get(f"/api/v1/marketplace/creators/{creator.id}/dashboard")
    assert response.status_code == 401


def test_invalid_token_rejected(app_client, db_session):
    creator = _make_user(db_session)
    _make_marketplace_template(db_session, creator)

    response = app_client.get(
        f"/api/v1/marketplace/creators/{creator.id}/dashboard",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


def test_another_user_rejected_non_enumerating(app_client, db_session):
    """A different, real, authenticated user requesting someone else's
    dashboard gets the same 404 as a dashboard for a creator_id that has no
    marketplace data at all — proving this route doesn't leak whether a
    given creator_id has private financial data."""
    creator = _make_user(db_session)
    _make_marketplace_template(db_session, creator)
    stranger = _make_user(db_session)
    token = create_access_token(stranger.id)

    response = app_client.get(
        f"/api/v1/marketplace/creators/{creator.id}/dashboard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert "estimated_mrr_cents" not in response.text
    assert "payout_status" not in response.text


def test_nonexistent_creator_rejected_with_same_signature(app_client, db_session):
    """A caller authenticated as themselves, requesting a creator_id that
    isn't their own (here: a creator_id that doesn't even correspond to a
    real user), gets the identical 404 as test_another_user_rejected —
    non-enumerating regardless of whether the target creator_id is real."""
    caller = _make_user(db_session)
    token = create_access_token(caller.id)

    response = app_client.get(
        f"/api/v1/marketplace/creators/{uuid.uuid4()}/dashboard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


def test_disabled_creator_rejected(app_client, db_session):
    creator = _make_user(db_session)
    creator.is_active = False
    db_session.flush()
    _make_marketplace_template(db_session, creator)
    token = create_access_token(creator.id)

    response = app_client.get(
        f"/api/v1/marketplace/creators/{creator.id}/dashboard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_owner_sees_own_dashboard(app_client, db_session):
    creator = _make_user(db_session)
    _make_marketplace_template(db_session, creator)
    db_session.add(CreatorConnectAccount(
        creator_id=creator.id, stripe_account_id=f"acct_{uuid.uuid4().hex[:16]}",
        details_submitted=True, charges_enabled=True, payouts_enabled=True,
    ))
    db_session.flush()
    token = create_access_token(creator.id)

    response = app_client.get(
        f"/api/v1/marketplace/creators/{creator.id}/dashboard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["creator_id"] == str(creator.id)
    assert body["template_count"] == 1
    assert body["payout_status"] == "ready"


def test_owner_with_no_listings_sees_empty_dashboard(app_client, db_session):
    """The template_count == 0 early-return path (no MarketplaceTemplate
    rows for this creator yet) is also gated by the same auth check."""
    creator = _make_user(db_session)
    token = create_access_token(creator.id)

    response = app_client.get(
        f"/api/v1/marketplace/creators/{creator.id}/dashboard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["template_count"] == 0
    assert body["payout_status"] == "not_connected"
