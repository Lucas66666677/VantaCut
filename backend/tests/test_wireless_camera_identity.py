"""Mixed-auth security fix: POST and GET
/timelines/{timeline_id}/wireless-cameras/pairings (create_pairing and
list_pairings) in app/api/v1/wireless_cameras.py.

Both routes previously had NO verified caller identity at all: they took a
plain `user_id` (body field on create_pairing, bare query parameter on
list_pairings) and passed it straight into `_owned_timeline`'s ownership
check with zero cross-check that the request actually came from that user.
create_pairing additionally has a real, unauthenticated frontend caller
today (frontend/features/camera-ingest/wireless-camera-panel.tsx), so this
was a live, exploitable vulnerability, not merely a theoretical one: anyone
who could guess or learn a victim's user_id and a timeline_id belonging to
that victim could create wireless-camera pairings (real DB writes plus a
capability token) as that victim, or (via list_pairings) enumerate that
victim's existing pairings.

Both routes now require current_user (get_current_user) and derive the
ownership-check identity exclusively from current_user.id.

This file is not touching, and does not need to touch, any of the
device/capability-token protocol routes in this same file (start_recording,
read_pairing_clock, upload_recording_chunk, complete_recording,
relay_webrtc_signalling) — none of them import get_current_user, none of
them take a user-identity field to spoof, and all of them are still gated
by the same per-pairing capability token issued by create_pairing
(verify_wireless_camera_token / _token_or_401). One short regression test
below confirms that gate is unchanged.

Identity (get_current_user) and the ownership check (_owned_timeline) run
for real against the test database, never mocked. Nothing needs to be
mocked at an external boundary here: qr_code_data_uri and
issue_wireless_camera_token are both local, dependency-free (beyond the
`qrcode` package) computations with no network/SDK call.
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


def _load_wireless_camera_router():
    """Load app/api/v1/wireless_cameras.py directly, bypassing
    app.api.__init__ — same reasoning as every other identity-fix test file
    in this program."""
    module_path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "wireless_cameras.py"
    spec = importlib.util.spec_from_file_location("_vantacut_test_wireless_camera_router", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_wireless_module = _load_wireless_camera_router()


@pytest.fixture()
def app_client(db_session):
    app = FastAPI()
    app.include_router(_wireless_module.router, prefix=API_V1)
    app.include_router(_wireless_module.mobile_router, prefix=API_V1)

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


def _make_user(db_session) -> User:
    user = User(email=f"wireless-camera-{uuid.uuid4().hex[:12]}@example.com", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session, owner: User) -> Project:
    project = Project(owner_id=owner.id, name="Wireless camera identity test project")
    db_session.add(project)
    db_session.flush()
    return project


def _make_timeline(db_session, project: Project) -> Timeline:
    timeline = Timeline(project_id=project.id, name="Wireless camera identity test timeline")
    db_session.add(timeline)
    db_session.flush()
    return timeline


def _auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _create_pairing(app_client, timeline_id, headers=None, **body_overrides):
    body = {"label": "測試鏡頭"}
    body.update(body_overrides)
    return app_client.post(f"{API_V1}/timelines/{timeline_id}/wireless-cameras/pairings", json=body, headers=headers or {})


def _list_pairings(app_client, timeline_id, headers=None, **query_overrides):
    return app_client.get(f"{API_V1}/timelines/{timeline_id}/wireless-cameras/pairings", params=query_overrides, headers=headers or {})


# --- create_pairing ----------------------------------------------------------


def test_create_pairing_anonymous_rejected(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)

    response = _create_pairing(app_client, timeline.id)
    assert response.status_code == 401
    assert db_session.query(CameraDevice).filter_by(project_id=project.id).first() is None
    assert db_session.query(CameraIngestSession).filter_by(project_id=project.id).first() is None


def test_create_pairing_invalid_token_rejected(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)

    response = _create_pairing(app_client, timeline.id, headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401
    assert db_session.query(CameraDevice).filter_by(project_id=project.id).first() is None


def test_create_pairing_non_owner_rejected_zero_side_effects(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    stranger = _make_user(db_session)

    response = _create_pairing(app_client, timeline.id, headers=_auth(stranger))
    assert response.status_code == 403
    assert db_session.query(CameraDevice).filter_by(project_id=project.id).first() is None
    assert db_session.query(CameraIngestSession).filter_by(project_id=project.id).first() is None
    db_session.refresh(timeline)
    assert not (timeline.settings_json or {}).get("wireless_multicam")


def test_create_pairing_spoofed_legacy_user_id_has_no_effect(app_client, db_session):
    """The request schema no longer accepts a user_id field at all — a
    non-owner who supplies the real owner's id in the request body is still
    rejected."""
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    attacker = _make_user(db_session)

    response = _create_pairing(app_client, timeline.id, headers=_auth(attacker), user_id=str(owner.id))
    assert response.status_code == 403
    assert db_session.query(CameraDevice).filter_by(project_id=project.id).first() is None


def test_create_pairing_rightful_owner_succeeds(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)

    response = _create_pairing(app_client, timeline.id, headers=_auth(owner), label="鏡頭 A")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["label"] == "鏡頭 A"
    assert "token=" in body["mobile_url"]

    device = db_session.query(CameraDevice).filter_by(project_id=project.id).first()
    session = db_session.query(CameraIngestSession).filter_by(project_id=project.id).first()
    assert device is not None
    assert session is not None
    db_session.refresh(timeline)
    assert len((timeline.settings_json or {}).get("wireless_multicam", {}).get("cameras", [])) == 1


# --- list_pairings -------------------------------------------------------------


def test_list_pairings_anonymous_rejected(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)

    response = _list_pairings(app_client, timeline.id)
    assert response.status_code == 401


def test_list_pairings_non_owner_rejected_non_enumerating(app_client, db_session):
    """Previously an IDOR: any caller who supplied the owner's user_id as a
    plain query parameter could enumerate that owner's pairings. Now a
    stranger's own verified identity is used, and they are rejected
    regardless of what user_id they might still try to pass."""
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    stranger = _make_user(db_session)

    response = _list_pairings(app_client, timeline.id, headers=_auth(stranger), user_id=str(owner.id))
    assert response.status_code == 403


def test_list_pairings_rightful_owner_succeeds(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    created = _create_pairing(app_client, timeline.id, headers=_auth(owner), label="鏡頭 B")
    assert created.status_code == 201

    response = _list_pairings(app_client, timeline.id, headers=_auth(owner))
    assert response.status_code == 200
    pairings = response.json()
    assert len(pairings) == 1
    assert pairings[0]["label"] == "鏡頭 B"


# --- sibling capability-token protocol route regression -----------------------


def test_start_recording_sibling_route_still_requires_capability_token_not_user_auth(app_client, db_session):
    """start_recording (and its siblings read_pairing_clock,
    upload_recording_chunk, complete_recording) are unaffected by this fix:
    they take no user-identity field and remain gated purely by the
    per-pairing capability token minted by create_pairing. Confirm a
    request with a valid *user* bearer token but no capability token is
    still rejected — proves this fix did not accidentally add a
    get_current_user requirement to the device-protocol routes."""
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    created = _create_pairing(app_client, timeline.id, headers=_auth(owner))
    pairing_id = created.json()["pairing_id"]

    response = app_client.post(
        f"{API_V1}/wireless-cameras/pairings/{pairing_id}/start",
        json={"server_aligned_started_at_ms": 0},
        headers=_auth(owner),  # a valid user token, deliberately not a capability token
    )
    assert response.status_code == 401
