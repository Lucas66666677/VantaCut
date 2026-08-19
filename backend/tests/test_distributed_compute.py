"""Batch 2B security tests for POST /compute/nodes (app/api/v1/distributed_compute.py).

POST /nodes is an end-user ENROLLMENT action (a person opting their own
browser/desktop into the compute pool) — distinct from the compute-node
protocol itself, which authenticates every subsequent call (heartbeat,
assignment fetch, signed result submission) with an Ed25519 keypair via
`verify_node_signature`/`verify_ticket`. This batch only changes who
enrollment is attributed to (get_current_user instead of a client-supplied
`owner_id`); it does not touch, weaken, or replace the node-signature
protocol. test_heartbeat_still_requires_node_signature below is a narrow
regression check proving that protocol is untouched.

Identity (get_current_user) runs for real against the test database, never
mocked, matching every other Batch 1/2A/2B security test file.
"""
from __future__ import annotations

import base64
import importlib.util
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db.session import get_db
from app.models.entities import ComputeNode, ComputeNodeStatus, User


def _load_distributed_compute_router():
    """Load app/api/v1/distributed_compute.py directly, bypassing
    `app.api.__init__` — same reasoning as every other Batch 1/2A/2B test
    file. This module imports app.tasks.distributed_compute_tasks (celery,
    already installed for this CI slice since Batch 2A) and
    app.services.storage (boto3, likewise already installed) at module
    level; neither pulls in any ML/render dependency.
    """
    module_path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "distributed_compute.py"
    spec = importlib.util.spec_from_file_location("_vantacut_test_distributed_compute_router", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_compute_module = _load_distributed_compute_router()


@pytest.fixture()
def app_client(db_session):
    app = FastAPI()
    app.include_router(_compute_module.router, prefix="/api/v1")

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


def _b64_ed25519_public_key() -> str:
    # enroll_compute_node only checks length == 32 raw bytes; this test
    # suite never verifies an actual Ed25519 signature over this key
    # (that's exercised by app/services/distributed_compute.py's own unit
    # coverage, unchanged by this batch), so 32 arbitrary bytes are enough.
    return base64.b64encode(uuid.uuid4().bytes + uuid.uuid4().bytes).decode("ascii")


def _enroll_body(**overrides) -> dict:
    body = {
        "label": "Batch 2B test node",
        "public_key": _b64_ed25519_public_key(),
        "node_kind": "browser",
        "capabilities": {},
        "consent": {"explicit_opt_in": True},
    }
    body.update(overrides)
    return body


def test_anonymous_rejected(app_client, db_session):
    response = app_client.post("/api/v1/compute/nodes", json=_enroll_body())
    assert response.status_code == 401


def test_invalid_token_rejected(app_client, db_session):
    response = app_client.post(
        "/api/v1/compute/nodes",
        json=_enroll_body(),
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


def test_client_cannot_enroll_node_as_another_users_owner(app_client, db_session):
    """The request schema no longer accepts an owner_id field at all — a
    caller who supplies one is simply ignored (extra body fields are
    dropped by the pydantic schema), and the node is always enrolled under
    the authenticated caller, never a client-supplied identity."""
    caller = _make_user(db_session)
    victim = _make_user(db_session)
    token = create_access_token(caller.id)

    response = app_client.post(
        "/api/v1/compute/nodes",
        json={**_enroll_body(), "owner_id": str(victim.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    node = db_session.get(ComputeNode, uuid.UUID(response.json()["node_id"]))
    assert node.owner_id == caller.id
    assert node.owner_id != victim.id


def test_disabled_user_rejected(app_client, db_session):
    caller = _make_user(db_session)
    caller.is_active = False
    db_session.flush()
    token = create_access_token(caller.id)

    response = app_client.post(
        "/api/v1/compute/nodes",
        json=_enroll_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_authenticated_enrollment_stores_current_user_as_owner(app_client, db_session):
    caller = _make_user(db_session)
    token = create_access_token(caller.id)

    response = app_client.post(
        "/api/v1/compute/nodes",
        json=_enroll_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    node = db_session.get(ComputeNode, uuid.UUID(response.json()["node_id"]))
    assert node.owner_id == caller.id
    assert node.status == ComputeNodeStatus.ACTIVE


def test_missing_explicit_consent_rejected(app_client, db_session):
    """Unchanged pre-existing validation (app/schemas/distributed_compute.py's
    explicit_consent_required) still runs — this batch doesn't touch it."""
    caller = _make_user(db_session)
    token = create_access_token(caller.id)

    response = app_client.post(
        "/api/v1/compute/nodes",
        json=_enroll_body(consent={}),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


def test_heartbeat_still_requires_node_signature(app_client, db_session):
    """Regression check: the enrollment fix does not touch the existing
    node-signature protocol used by heartbeat/assignments/signal routes. An
    enrolled node's heartbeat call with a bogus signature is still rejected
    exactly as before this batch."""
    caller = _make_user(db_session)
    token = create_access_token(caller.id)
    enroll_response = app_client.post(
        "/api/v1/compute/nodes",
        json=_enroll_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert enroll_response.status_code == 201
    node_id = enroll_response.json()["node_id"]

    response = app_client.post(
        f"/api/v1/compute/nodes/{node_id}/heartbeat",
        json={
            "available": True,
            "capabilities": {},
            "signature": "0" * 32,
            "signed_at": "2026-01-01T00:00:00Z",
        },
    )
    assert response.status_code == 401
