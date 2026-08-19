"""Batch 2A security tests for app/api/v1/collaboration.py (real-time
timeline collaboration WebSocket).

Identity (`_authenticate_websocket`, reused unmodified from
project_status.py — see that module for its own coverage) and authorization
(`_authorize_timeline_access` — project owner OR a `ReviewParticipant` row
for this specific timeline; see that function's docstring for the evidence
this reuses, not invents) are exercised for real against the test database.

`collaboration_hub` (the Redis-backed pub/sub relay) is replaced with a
lightweight recording fake in every test here — not to fake identity or
authorization, but because the real hub's `join()` starts a background
`asyncio.create_task` Redis-subscription loop that has no natural end until
the room empties, which is exactly the class of hang risk
test_project_status.py already hit and had to work around for its
`while True` WebSocket loop. Mocking the hub sidesteps that risk entirely
(no background task is ever created) while still giving a concrete,
non-inferred assertion: an unauthorized caller never calls `hub.join(...)`,
so it never starts a pub/sub subscription or receives a single buffered Yjs
update, regardless of anything else in this test's assertions.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.security import create_access_token
from app.db.session import get_db
from app.models.entities import Project, ReviewParticipant, ReviewRole, Timeline, User


def _load_collaboration_router():
    """Load app/api/v1/collaboration.py directly, bypassing `app.api.__init__`
    — same reasoning as tests/conftest.py::_load_auth_router. This module
    also imports app.api.v1.project_status (for `_authenticate_websocket`),
    which is loaded the same way by test_project_status.py — both imports
    resolve fine on their own; neither pulls in the heavier per-route
    dependencies (boto3, celery) that audio_description.py/
    audio_enhancement.py need.
    """
    module_path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "collaboration.py"
    spec = importlib.util.spec_from_file_location("_vantacut_test_collaboration_router", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_collab_module = _load_collaboration_router()


class _RecordingHub:
    def __init__(self) -> None:
        self.joined: list[str] = []
        self.left: list[str] = []

    async def join(self, timeline_id: str, websocket) -> None:  # noqa: ANN001
        self.joined.append(timeline_id)

    async def leave(self, timeline_id: str, websocket) -> None:  # noqa: ANN001
        self.left.append(timeline_id)

    async def publish_update(self, timeline_id: str, update: bytes) -> None:  # noqa: ANN001
        raise AssertionError("not exercised by these security tests")

    async def publish_presence(self, timeline_id: str, payload: str) -> None:  # noqa: ANN001
        raise AssertionError("not exercised by these security tests")


@pytest.fixture()
def hub(monkeypatch):
    fake = _RecordingHub()
    monkeypatch.setattr(_collab_module, "collaboration_hub", fake)
    return fake


@pytest.fixture()
def app_client(db_session):
    app = FastAPI()
    app.include_router(_collab_module.router, prefix="/api/v1")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


def _make_user(db_session) -> User:
    user = User(email=f"batch2a-{uuid.uuid4().hex[:12]}@example.com", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session, owner: User) -> Project:
    project = Project(owner_id=owner.id, name="Batch 2A collaboration test project")
    db_session.add(project)
    db_session.flush()
    return project


def _make_timeline(db_session, project: Project) -> Timeline:
    timeline = Timeline(project_id=project.id, name="Batch 2A test timeline")
    db_session.add(timeline)
    db_session.flush()
    return timeline


def test_ws_no_credential_rejected_before_accept(app_client, db_session, hub):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)

    with pytest.raises(WebSocketDisconnect):
        with app_client.websocket_connect(f"/api/v1/timelines/{timeline.id}/collaboration"):
            pass
    assert hub.joined == []


def test_ws_invalid_credential_rejected_before_accept(app_client, db_session, hub):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)

    with pytest.raises(WebSocketDisconnect):
        with app_client.websocket_connect(
            f"/api/v1/timelines/{timeline.id}/collaboration",
            subprotocols=["bearer", "not-a-real-token"],
        ):
            pass
    assert hub.joined == []


def test_ws_authenticated_unauthorized_user_rejected(app_client, db_session, hub):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    stranger = _make_user(db_session)
    token = create_access_token(stranger.id)

    with pytest.raises(WebSocketDisconnect):
        with app_client.websocket_connect(
            f"/api/v1/timelines/{timeline.id}/collaboration",
            subprotocols=["bearer", token],
        ):
            pass
    assert hub.joined == []


def test_ws_unknown_timeline_rejected_non_enumerating(app_client, db_session, hub):
    owner = _make_user(db_session)
    token = create_access_token(owner.id)

    with pytest.raises(WebSocketDisconnect):
        with app_client.websocket_connect(
            f"/api/v1/timelines/{uuid.uuid4()}/collaboration",
            subprotocols=["bearer", token],
        ):
            pass
    assert hub.joined == []


def test_ws_owner_accepted(app_client, db_session, hub):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    token = create_access_token(owner.id)

    with app_client.websocket_connect(
        f"/api/v1/timelines/{timeline.id}/collaboration",
        subprotocols=["bearer", token],
    ) as websocket:
        assert websocket.accepted_subprotocol == "bearer"
    assert hub.joined == [str(timeline.id)]


def test_ws_non_owner_review_participant_accepted(app_client, db_session, hub):
    """Proves this fix does not silently narrow collaboration to owner-only:
    a real, pre-existing non-owner access grant (ReviewParticipant) must
    still be able to join. If this test is ever changed to expect rejection,
    that is a product-semantics change, not a security hardening — see
    _authorize_timeline_access's docstring for why REVIEWER is included."""
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    reviewer = _make_user(db_session)
    db_session.add(ReviewParticipant(timeline_id=timeline.id, user_id=reviewer.id, role=ReviewRole.REVIEWER))
    db_session.flush()
    token = create_access_token(reviewer.id)

    with app_client.websocket_connect(
        f"/api/v1/timelines/{timeline.id}/collaboration",
        subprotocols=["bearer", token],
    ) as websocket:
        assert websocket.accepted_subprotocol == "bearer"
    assert hub.joined == [str(timeline.id)]
