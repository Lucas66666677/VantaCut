"""Real-JWT ownership coverage for live-director signalling and control."""
from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.security import create_access_token
from app.db.session import get_db
from app.models.entities import Project, User

API = "/api/v1"


class _StubLiveDirectorError(RuntimeError):
    pass


# The auth CI slice deliberately omits aiortc. Replace only that infrastructure
# module while loading the route; authentication and DB ownership stay real.
_live_service = types.ModuleType("app.services.live_director")
_live_service.LiveDirectorError = _StubLiveDirectorError
_live_service.live_directors = object()
sys.modules.setdefault("app.services.live_director", _live_service)


def _load():
    path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "live.py"
    spec = importlib.util.spec_from_file_location("_vantacut_m13_live", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


live = _load()


class _Director:
    def __init__(self, session_id, project_id, effects):
        self.session_id = session_id
        self.project_id = project_id
        self.status = "created"
        self.layout = "single"
        self.active_camera_id = None
        self.sources = {}
        self.caption = None
        self.effects = effects
        self.snapshot_calls = 0
        self.disconnect_at = None

    async def start(self):
        self.effects.append("start")
        self.status = "live"

    async def add_websocket_offer(self, camera_id, _sdp, is_wide):
        self.effects.append("webrtc")
        self.sources[camera_id] = {"camera_id": camera_id, "is_wide_camera": is_wide}
        return SimpleNamespace(sdp="answer-sdp")

    async def attach_gateway_source(self, camera_id, _url, is_wide):
        self.effects.append("gateway")
        self.sources[camera_id] = {"camera_id": camera_id, "is_wide_camera": is_wide}

    def set_caption(self, text, emotion, animation, ttl):
        self.effects.append("caption")
        self.caption = {"text": text, "emotion": emotion, "animation_preset": animation, "ttl_seconds": ttl}

    def set_override(self, layout, camera_id):
        self.effects.append("director")
        self.layout = "single" if layout == "auto" else layout
        self.active_camera_id = camera_id

    def snapshot(self):
        self.snapshot_calls += 1
        if self.disconnect_at is not None and self.snapshot_calls >= self.disconnect_at:
            raise WebSocketDisconnect(code=1000)
        return {
            "session_id": self.session_id,
            "status": self.status,
            "layout": self.layout,
            "active_camera_id": self.active_camera_id,
            "sources": list(self.sources.values()),
            "caption": self.caption,
        }


class _Registry:
    def __init__(self):
        self.directors = {}
        self.effects = []

    def create(self, *, session_id, project_id, **_kwargs):
        self.effects.append("create")
        director = _Director(session_id, project_id, self.effects)
        self.directors[session_id] = director
        return director

    def get(self, session_id):
        if session_id not in self.directors:
            raise live.LiveDirectorError("Live session not found")
        return self.directors[session_id]

    async def stop(self, session_id):
        director = self.get(session_id)
        self.effects.append("stop")
        director.status = "stopped"


def _client(db_session) -> TestClient:
    app = FastAPI()
    app.include_router(live.router, prefix=API)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _user(db_session) -> User:
    user = User(email=f"m13-{uuid.uuid4().hex[:16]}@example.com", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


def _auth(user):
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _project(db_session, owner):
    project = Project(owner_id=owner.id, name="M13 live session")
    db_session.add(project)
    db_session.flush()
    return project


def _requests(project, session_id, spoof_user):
    return [
        ("post", f"{API}/live/sessions", {"user_id": str(spoof_user.id), "project_id": str(project.id), "title": "Live", "width": 1280, "height": 720, "fps": 30}),
        ("post", f"{API}/live/sessions/{session_id}/webrtc/offer", {"camera_id": "phone", "sdp": "offer-sdp", "type": "offer"}),
        ("post", f"{API}/live/sessions/{session_id}/sources/gateway", {"camera_id": "obs"}),
        ("post", f"{API}/live/sessions/{session_id}/captions", {"text": "Hello", "emotion": "neutral"}),
        ("post", f"{API}/live/sessions/{session_id}/director", {"layout": "auto"}),
        ("get", f"{API}/live/sessions/{session_id}", None),
        ("delete", f"{API}/live/sessions/{session_id}", None),
    ]


def _request(client, method, url, body, headers):
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    return getattr(client, method)(url, **kwargs)


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer malformed"}])
def test_m13_http_endpoints_reject_anonymous_and_invalid_jwts_without_side_effects(db_session, monkeypatch, headers):
    owner, spoof = _user(db_session), _user(db_session)
    project, registry = _project(db_session, owner), _Registry()
    registry.directors["existing"] = _Director("existing", str(project.id), registry.effects)
    monkeypatch.setattr(live, "live_directors", registry)
    client = _client(db_session)
    for method, url, body in _requests(project, "existing", spoof):
        response = _request(client, method, url, body, headers)
        assert response.status_code == 401, (method, url, response.text)
    assert registry.effects == []


def test_m13_wrong_user_cannot_spoof_owner_or_trigger_live_side_effects(db_session, monkeypatch):
    owner, attacker = _user(db_session), _user(db_session)
    project, registry = _project(db_session, owner), _Registry()
    registry.directors["existing"] = _Director("existing", str(project.id), registry.effects)
    monkeypatch.setattr(live, "live_directors", registry)
    client = _client(db_session)
    for method, url, body in _requests(project, "existing", owner):
        response = _request(client, method, url, body, _auth(attacker))
        assert response.status_code == 403, (method, url, response.text)
    assert registry.effects == []


def test_m13_rightful_owner_can_use_every_live_http_endpoint(db_session, monkeypatch):
    owner, spoof = _user(db_session), _user(db_session)
    project, registry = _project(db_session, owner), _Registry()
    registry.directors["existing"] = _Director("existing", str(project.id), registry.effects)
    monkeypatch.setattr(live, "live_directors", registry)
    client = _client(db_session)
    for method, url, body in _requests(project, "existing", spoof):
        response = _request(client, method, url, body, _auth(owner))
        assert response.status_code in {200, 201, 204}, (method, url, response.status_code, response.text)
        if response.status_code == 204:
            assert response.content == b""
    assert registry.effects == ["create", "start", "webrtc", "gateway", "caption", "director", "stop"]


@pytest.mark.parametrize("protocols", [None, ["bearer", "malformed"]])
def test_m13_control_websocket_rejects_missing_or_invalid_credentials(db_session, monkeypatch, protocols):
    owner = _user(db_session)
    project, registry = _project(db_session, owner), _Registry()
    registry.directors["existing"] = _Director("existing", str(project.id), registry.effects)
    monkeypatch.setattr(live, "live_directors", registry)
    with pytest.raises(WebSocketDisconnect):
        with _client(db_session).websocket_connect(f"{API}/live/sessions/existing/control/ws", subprotocols=protocols):
            pass


def test_m13_control_websocket_rejects_wrong_owner_and_accepts_owner(db_session, monkeypatch):
    owner, attacker = _user(db_session), _user(db_session)
    project, registry = _project(db_session, owner), _Registry()
    director = _Director("existing", str(project.id), registry.effects)
    registry.directors["existing"] = director
    monkeypatch.setattr(live, "live_directors", registry)
    client = _client(db_session)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"{API}/live/sessions/existing/control/ws", subprotocols=["bearer", create_access_token(attacker.id)]):
            pass
    director.disconnect_at = director.snapshot_calls + 2
    with client.websocket_connect(f"{API}/live/sessions/existing/control/ws", subprotocols=["bearer", create_access_token(owner.id)]) as websocket:
        assert websocket.accepted_subprotocol == "bearer"
        assert websocket.receive_json()["session_id"] == "existing"
