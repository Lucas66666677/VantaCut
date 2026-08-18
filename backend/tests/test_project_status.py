"""Batch 1 security tests for app/api/v1/project_status.py (SSE + WebSocket).

Identity (get_current_user / _authenticate_websocket) and ownership
(_authorize_project / the inline WS ownership check) are exercised for real
against the test database. The Redis pub/sub transport is mocked ONLY in the
two "owner succeeds" happy-path tests, so those tests don't require a live
Redis instance to prove that auth+ownership pass and the connection is
actually accepted/streamed — never to fake identity or ownership. Every
rejection-path test below never constructs a Redis client at all (the route
code checks auth/ownership before ever touching Redis), so those tests
exercise zero mocked infrastructure.
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
from app.models.entities import Project, User


def _load_project_status_router():
    """Load app/api/v1/project_status.py directly, bypassing `app.api.__init__`
    — same reasoning as tests/conftest.py::_load_auth_router."""
    module_path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "project_status.py"
    spec = importlib.util.spec_from_file_location("_vantacut_test_project_status_router", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ps_module = _load_project_status_router()


class _FakePubSub:
    async def subscribe(self, *_args, **_kwargs) -> None:
        return None

    async def get_message(self, *_args, **_kwargs):
        # Always "no message" -> callers fall through to their keepalive branch.
        return None

    async def unsubscribe(self, *_args, **_kwargs) -> None:
        return None

    async def aclose(self) -> None:
        return None


class _FakeRedisClient:
    """Stands in for the real Redis connection so the two happy-path tests
    below don't need a live Redis instance to prove auth+ownership pass and
    the stream/socket is actually accepted. Never used by any rejection-path
    test — those never reach the code that constructs a Redis client."""

    async def get(self, *_args, **_kwargs):
        return None

    def pubsub(self):
        return _FakePubSub()

    async def aclose(self) -> None:
        return None


@pytest.fixture()
def fake_redis(monkeypatch):
    monkeypatch.setattr(
        _ps_module.redis_async, "from_url", lambda *args, **kwargs: _FakeRedisClient()
    )


@pytest.fixture()
def app_client(db_session):
    app = FastAPI()
    app.include_router(_ps_module.router, prefix="/api/v1")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


def _make_user(db_session) -> User:
    user = User(email=f"batch1-{uuid.uuid4().hex[:12]}@example.com", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session, owner: User) -> Project:
    project = Project(owner_id=owner.id, name="Batch 1 status test project")
    db_session.add(project)
    db_session.flush()
    return project


# ---------------------------------------------------------------------------
# SSE: GET /projects/{project_id}/status
# ---------------------------------------------------------------------------


def test_sse_anonymous_rejected(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)

    response = app_client.get(f"/api/v1/projects/{project.id}/status")
    assert response.status_code == 401


def test_sse_invalid_token_rejected(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)

    response = app_client.get(
        f"/api/v1/projects/{project.id}/status",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


def test_sse_wrong_owner_rejected(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    other_user = _make_user(db_session)
    token = create_access_token(other_user.id)

    response = app_client.get(
        f"/api/v1/projects/{project.id}/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


async def _always_disconnected(self) -> bool:  # noqa: ANN001 - matches Request.is_disconnected's signature
    return True


def test_sse_owner_accepted(app_client, db_session, fake_redis, monkeypatch):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    token = create_access_token(owner.id)

    # CI evidence (Batch 1 fix 1/3): this test hung for the full 15-minute job
    # timeout instead of completing. Root cause: `project_status_events`
    # yields its one "event: status" chunk BEFORE entering
    # `while not await request.is_disconnected(): ...`, but TestClient's
    # synchronous streaming transport does not reliably deliver an ASGI
    # "http.disconnect" back into that generator when a test — like this one
    # — reads only the first chunk and then closes the stream. The generator
    # is then left spinning in its keepalive loop for the rest of the test
    # process's life, which is exactly the risk flagged as unverified in
    # vantacut-batch1-checkpoint.md ("whether TestClient.stream(...) cleanly
    # cancels the SSE generator on early client-side close"). It doesn't.
    #
    # Forcing is_disconnected() to report True lets the generator fall
    # through to its `finally:` cleanup right after the one yield this test
    # needs, instead of ever entering the keepalive loop. This is a
    # TestClient/ASGI-transport limitation fix, not a security change: auth
    # and ownership (_authorize_project) already ran and passed before the
    # StreamingResponse was even constructed, and are untouched here.
    monkeypatch.setattr(_ps_module.Request, "is_disconnected", _always_disconnected)

    with app_client.stream(
        "GET",
        f"/api/v1/projects/{project.id}/status",
        headers={"Authorization": f"Bearer {token}"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        first_line = next(response.iter_lines())
        assert first_line == "event: status"


# ---------------------------------------------------------------------------
# WebSocket: WS /projects/{project_id}/status/ws
# ---------------------------------------------------------------------------


def test_ws_no_credential_rejected_before_accept(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)

    with pytest.raises(WebSocketDisconnect):
        with app_client.websocket_connect(f"/api/v1/projects/{project.id}/status/ws"):
            pass


def test_ws_invalid_credential_rejected_before_accept(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)

    with pytest.raises(WebSocketDisconnect):
        with app_client.websocket_connect(
            f"/api/v1/projects/{project.id}/status/ws",
            subprotocols=["bearer", "not-a-real-token"],
        ):
            pass


def test_ws_authenticated_non_owner_rejected(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    other_user = _make_user(db_session)
    token = create_access_token(other_user.id)

    with pytest.raises(WebSocketDisconnect):
        with app_client.websocket_connect(
            f"/api/v1/projects/{project.id}/status/ws",
            subprotocols=["bearer", token],
        ):
            pass


async def _raise_disconnect(self, *_args, **_kwargs):
    raise WebSocketDisconnect(code=1000)


def test_ws_authenticated_owner_accepted(app_client, db_session, fake_redis, monkeypatch):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    token = create_access_token(owner.id)

    # CI fix (Batch 1, same root cause class as test_sse_owner_accepted): the
    # server's WS loop only ever calls websocket.send_*, never
    # websocket.receive(), so it has no way to observe this test closing the
    # connection and would otherwise spin in `while True` for the rest of the
    # test process's life. This test's one required assertion (the "bearer"
    # subprotocol plus the initial status payload) is already fully satisfied
    # by websocket.send_text(...) BEFORE the loop is ever entered. Making the
    # mocked pubsub raise WebSocketDisconnect on its first poll exercises the
    # route's existing `except WebSocketDisconnect: pass` cleanup path
    # (already written to handle real client disconnects) so the handler
    # coroutine actually returns instead of hanging. Auth and ownership are
    # untouched — both already passed before websocket.accept() ran.
    monkeypatch.setattr(_FakePubSub, "get_message", _raise_disconnect)

    with app_client.websocket_connect(
        f"/api/v1/projects/{project.id}/status/ws",
        subprotocols=["bearer", token],
    ) as websocket:
        assert websocket.accepted_subprotocol == "bearer"
        payload = websocket.receive_text()
        assert '"project_id"' in payload
