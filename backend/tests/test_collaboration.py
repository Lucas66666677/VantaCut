"""Batch 2A security tests for app/api/v1/collaboration.py (real-time
timeline collaboration WebSocket).

Identity (`authenticate_websocket_bearer`, shared with project_status.py via
app.auth.websocket — see that module's docstring for why it was extracted
there during this batch, and test_project_status.py for that route's own
coverage of the same function) and authorization
(`_authorize_timeline_access` — project owner ONLY; see that function's
docstring for why an earlier "owner OR ReviewParticipant" version of this
function was replaced after a focused follow-up review found the WebSocket
grants real, bidirectional live-editing capability that ReviewParticipant
is never evidenced to carry anywhere else in the codebase) are exercised
for real against the test database.

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

CI evidence note: test_ws_non_owner_review_participant_rejected still sets
ReviewParticipant.created_at/updated_at explicitly rather than relying on
the ORM's declared server_default, purely to construct the row at all —
see that test's docstring for the pre-existing, production-affecting
migration bug this works around without touching the migration here (fixed
separately by a stacked schema migration; see that PR).
"""
from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timezone
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
    — same reasoning as tests/conftest.py::_load_auth_router.

    collaboration.py imports `app.auth.websocket.authenticate_websocket_bearer`
    rather than importing it from app.api.v1.project_status directly: an
    earlier version of this module did `from app.api.v1.project_status
    import _authenticate_websocket`, and CI proved (ModuleNotFoundError:
    'torch', via app.api.v1.analysis -> ... -> app.ml.retention_model) that
    ANY `from app.api.v1.X import ...` statement — even inside a module
    that is itself loaded by file path — forces Python to first import the
    `app.api`/`app.api.v1` *packages* to resolve the dotted path, which runs
    `app/api/__init__.py` and its eager import of all ~75 routers. Living
    under `app.auth` (whose own `__init__.py` is empty) avoids that. Neither
    that shared helper nor app.services.collaboration pulls in the heavier
    per-route dependencies (boto3, celery) that audio_description.py/
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


def test_ws_non_owner_review_participant_rejected(app_client, db_session, hub):
    """Proves ReviewParticipant does NOT grant live-editing collaboration
    access — corrected after a focused follow-up review found the earlier
    "owner OR ReviewParticipant" version of _authorize_timeline_access was
    overbroad. ReviewParticipant's only evidenced scope anywhere in this
    codebase (app/api/v1/reviews.py) is review comments and approve/reject
    decisions on separate ReviewComment/TimelineReview entities — never the
    actual Timeline/Clip content this WebSocket's Yjs channel authoritatively
    mutates. Extending ReviewParticipant to cover that would be inventing new
    permission semantics under an existing name, not reusing one. If this
    test is ever changed to expect acceptance, that must be a deliberate,
    evidenced product decision (e.g. a real, designed reviewer-collaboration
    feature), not a reversion to this incorrect assumption.

    created_at/updated_at are still set explicitly here, not left to the
    ORM's declared `server_default=func.now()`, purely to construct the row
    at all: migration 0012_add_review_approval.py's `review_participants`
    DDL declares both columns `nullable=False` with no `server_default`
    (unlike 0001_initial.py's tables, which all set
    `server_default=sa.func.now()` correctly) — a genuine, pre-existing
    schema bug, fixed separately by a stacked migration PR rather than here."""
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    reviewer = _make_user(db_session)
    now = datetime.now(timezone.utc)
    db_session.add(
        ReviewParticipant(
            timeline_id=timeline.id,
            user_id=reviewer.id,
            role=ReviewRole.REVIEWER,
            created_at=now,
            updated_at=now,
        )
    )
    db_session.flush()
    token = create_access_token(reviewer.id)

    with pytest.raises(WebSocketDisconnect):
        with app_client.websocket_connect(
            f"/api/v1/timelines/{timeline.id}/collaboration",
            subprotocols=["bearer", token],
        ):
            pass
    assert hub.joined == []


def test_ws_approver_review_participant_rejected(app_client, db_session, hub):
    """Same as above for the APPROVER role, to prove this isn't a
    reviewer-vs-approver distinction — neither review role gets live-editing
    collaboration access, only the project owner does."""
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    approver = _make_user(db_session)
    now = datetime.now(timezone.utc)
    db_session.add(
        ReviewParticipant(
            timeline_id=timeline.id,
            user_id=approver.id,
            role=ReviewRole.APPROVER,
            created_at=now,
            updated_at=now,
        )
    )
    db_session.flush()
    token = create_access_token(approver.id)

    with pytest.raises(WebSocketDisconnect):
        with app_client.websocket_connect(
            f"/api/v1/timelines/{timeline.id}/collaboration",
            subprotocols=["bearer", token],
        ):
            pass
    assert hub.joined == []
