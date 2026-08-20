"""Mixed-auth security fix: POST /camera-ingest/devices (register_camera_device)
and POST /camera-ingest/devices/{device_id}/sessions (start_camera_ingest_session)
in app/api/v1/camera_ingest.py.

Both routes previously trusted a client-supplied `user_id` field in the
request body for the "does this caller own the target project" check, gated
only by a single shared `X-Ingest-Management-Token` header (a static,
deployment-wide ops secret — see require_ingest_management_token's own
docstring: "Temporary control-plane guard until the main user/device
authorization layer is wired in."). That token proves the caller may
provision cameras at all; it does not, and never did, prove the caller is
any particular user. Any holder of the one shared management token could
therefore register a device or start an ingest session "as" any user whose
project id they knew, simply by supplying that user's id in the body.

This fix is a genuine two-factor model, not a JWT replacement of the
management token: both routes now require the pre-existing
require_ingest_management_token dependency AND a verified
current_user (get_current_user), and derive the ownership-check identity
exclusively from current_user.id. The management-token guard is completely
unchanged — it is proven still independently enforced below
(test_register_missing_management_token_rejected_even_with_valid_user_auth).

The device/capability-token protocol routes in this same file
(upload_camera_chunk, guarded by a per-chunk HMAC signature; and
complete_camera_ingest_session, guarded only by the management token, which
takes no user-identity field at all and so has nothing to spoof) are not
touched by this fix and are not exercised here beyond one short regression
check that complete_camera_ingest_session's auth model is unchanged.

Identity (get_current_user) and the ownership check run for real against the
test database, never mocked, matching every other identity-fix test file in
this program.
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
from app.models.entities import CameraDevice, CameraIngestSession, Project, Timeline, User

API_V1 = "/api/v1"
MANAGEMENT_TOKEN = "ci-test-camera-ingest-management-token-not-real"


def _load_camera_ingest_router():
    """Load app/api/v1/camera_ingest.py directly, bypassing app.api.__init__
    — same reasoning as every other identity-fix test file in this program
    (see test_render_offload_identity.py / test_marketplace_mixed_identity.py)."""
    module_path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "camera_ingest.py"
    spec = importlib.util.spec_from_file_location("_vantacut_test_camera_ingest_router", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_camera_module = _load_camera_ingest_router()


@pytest.fixture(autouse=True)
def _management_token(monkeypatch):
    # The route's require_ingest_management_token dependency reads
    # app.core.config.settings.ingest_management_token at call time.
    # camera_ingest.py imports the same `settings` singleton object, so
    # patching this attribute here is visible to the loaded route module too.
    monkeypatch.setattr(_camera_module.settings, "ingest_management_token", MANAGEMENT_TOKEN)


@pytest.fixture()
def app_client(db_session):
    app = FastAPI()
    app.include_router(_camera_module.router, prefix=API_V1)

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


def _make_user(db_session) -> User:
    user = User(email=f"camera-ingest-{uuid.uuid4().hex[:12]}@example.com", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session, owner: User) -> Project:
    project = Project(owner_id=owner.id, name="Camera ingest identity test project")
    db_session.add(project)
    db_session.flush()
    return project


def _make_device(db_session, project: Project, identifier: str = "cam-01") -> CameraDevice:
    device = CameraDevice(
        project_id=project.id,
        device_identifier=identifier,
        display_name="Test camera",
        device_type="camera",
        encrypted_hmac_secret="not-a-real-encrypted-secret",
        is_active=True,
    )
    db_session.add(device)
    db_session.flush()
    return device


def _auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _mgmt(extra: dict | None = None) -> dict:
    headers = {"X-Ingest-Management-Token": MANAGEMENT_TOKEN}
    if extra:
        headers.update(extra)
    return headers


def _register_body(project_id, **overrides) -> dict:
    body = {"project_id": str(project_id), "device_identifier": f"cam-{uuid.uuid4().hex[:8]}", "display_name": "New camera"}
    body.update(overrides)
    return body


def _register(app_client, headers, **body_overrides):
    project_id = body_overrides.pop("project_id")
    return app_client.post(f"{API_V1}/camera-ingest/devices", json=_register_body(project_id, **body_overrides), headers=headers)


def _start_session(app_client, device_id, headers, **body_overrides):
    body = {"capture_id": f"capture-{uuid.uuid4().hex[:8]}"}
    body.update(body_overrides)
    return app_client.post(f"{API_V1}/camera-ingest/devices/{device_id}/sessions", json=body, headers=headers)


# --- register_camera_device -------------------------------------------------


def test_register_anonymous_rejected(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)

    response = _register(app_client, _mgmt(), project_id=project.id)
    assert response.status_code == 401
    assert db_session.query(CameraDevice).filter_by(project_id=project.id).first() is None


def test_register_invalid_token_rejected(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)

    response = _register(app_client, _mgmt({"Authorization": "Bearer not-a-real-token"}), project_id=project.id)
    assert response.status_code == 401
    assert db_session.query(CameraDevice).filter_by(project_id=project.id).first() is None


def test_register_missing_management_token_rejected_even_with_valid_user_auth(app_client, db_session):
    """Two-factor model proof: a fully valid, rightful-owner bearer token is
    not enough on its own — the pre-existing management-token gate is
    completely unchanged by this fix."""
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)

    response = app_client.post(
        f"{API_V1}/camera-ingest/devices", json=_register_body(project.id), headers=_auth(owner)
    )
    assert response.status_code == 403
    assert db_session.query(CameraDevice).filter_by(project_id=project.id).first() is None


def test_register_non_owner_rejected_zero_db_mutation(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    stranger = _make_user(db_session)

    response = _register(app_client, _mgmt(_auth(stranger)), project_id=project.id)
    assert response.status_code == 403
    assert db_session.query(CameraDevice).filter_by(project_id=project.id).first() is None


def test_register_spoofed_legacy_user_id_has_no_effect(app_client, db_session):
    """The request schema no longer accepts a user_id field at all — a
    non-owner who supplies the real owner's id in the request body is still
    rejected, because caller identity now comes exclusively from the
    verified bearer token."""
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    attacker = _make_user(db_session)

    response = _register(app_client, _mgmt(_auth(attacker)), project_id=project.id, user_id=str(owner.id))
    assert response.status_code == 403
    assert db_session.query(CameraDevice).filter_by(project_id=project.id).first() is None


def test_register_rightful_owner_succeeds(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)

    response = _register(app_client, _mgmt(_auth(owner)), project_id=project.id, device_identifier="cam-rightful")
    assert response.status_code == 201, response.text
    body = response.json()
    assert "device_secret" in body

    device = db_session.query(CameraDevice).filter_by(project_id=project.id, device_identifier="cam-rightful").first()
    assert device is not None


# --- start_camera_ingest_session --------------------------------------------


def test_start_session_anonymous_rejected(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    device = _make_device(db_session, project)

    response = _start_session(app_client, device.id, _mgmt())
    assert response.status_code == 401
    assert db_session.query(CameraIngestSession).filter_by(device_id=device.id).first() is None


def test_start_session_non_owner_rejected_zero_db_mutation(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    device = _make_device(db_session, project)
    stranger = _make_user(db_session)

    response = _start_session(app_client, device.id, _mgmt(_auth(stranger)))
    assert response.status_code == 403
    assert db_session.query(CameraIngestSession).filter_by(device_id=device.id).first() is None
    # No orphan Timeline created for the unauthorized attempt either.
    assert db_session.query(Timeline).filter_by(project_id=project.id).first() is None


def test_start_session_spoofed_legacy_user_id_has_no_effect(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    device = _make_device(db_session, project)
    attacker = _make_user(db_session)

    response = _start_session(app_client, device.id, _mgmt(_auth(attacker)), user_id=str(owner.id))
    assert response.status_code == 403
    assert db_session.query(CameraIngestSession).filter_by(device_id=device.id).first() is None


def test_start_session_rightful_owner_succeeds(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    device = _make_device(db_session, project)

    response = _start_session(app_client, device.id, _mgmt(_auth(owner)))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["device_id"] == str(device.id)

    session = db_session.query(CameraIngestSession).filter_by(device_id=device.id).first()
    assert session is not None
    assert session.project_id == project.id


# --- sibling device/protocol route regression -------------------------------


def test_complete_session_sibling_route_still_only_requires_management_token(app_client, db_session):
    """complete_camera_ingest_session takes no user-identity field and is not
    part of this fix's scope — confirm its auth model (management token
    only, no current_user requirement) is unchanged."""
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    device = _make_device(db_session, project)
    timeline = Timeline(project_id=project.id)
    db_session.add(timeline)
    db_session.flush()
    session = CameraIngestSession(project_id=project.id, device_id=device.id, timeline_id=timeline.id, capture_id="cap-1")
    db_session.add(session)
    db_session.flush()

    anonymous = app_client.post(f"{API_V1}/camera-ingest/sessions/{session.id}/complete")
    assert anonymous.status_code == 403  # missing management token, not 401 — no user-auth dependency here

    completed = app_client.post(f"{API_V1}/camera-ingest/sessions/{session.id}/complete", headers=_mgmt())
    assert completed.status_code == 200, completed.text
