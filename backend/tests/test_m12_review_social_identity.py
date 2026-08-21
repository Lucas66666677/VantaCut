"""Real-JWT coverage for review collaboration and social publishing identity."""
from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db.session import get_db
from app.models.entities import (
    CommentStatus,
    Project,
    RenderJob,
    RenderStatus,
    ReviewComment,
    SocialAccount,
    SocialPlatform,
    SocialPost,
    Timeline,
    TimelineReview,
    User,
)

API = "/api/v1"


def _load(name: str):
    path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_vantacut_m12_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reviews, social = _load("reviews"), _load("social")


class _Task:
    id = "m12-task"


class _Redis:
    def __init__(self):
        self.values: dict[str, str] = {}

    def setex(self, key, _ttl, value):
        self.values[key] = value

    def getdel(self, key):
        return self.values.pop(key, None)


class _SocialClient:
    def authorization_url(self, **_kwargs):
        return "https://provider.example/authorize"


def _client(db_session) -> TestClient:
    app = FastAPI()
    app.include_router(reviews.router, prefix=API)
    app.include_router(social.router, prefix=API)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _user(db_session) -> User:
    user = User(email=f"m12-{uuid.uuid4().hex[:16]}@example.com", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


def _graph(db_session, owner):
    project = Project(owner_id=owner.id, name="M12 collaboration")
    db_session.add(project)
    db_session.flush()
    timeline = Timeline(project_id=project.id, name="M12 timeline", is_current=True)
    db_session.add(timeline)
    db_session.flush()
    comment = ReviewComment(
        project_id=project.id,
        timeline_id=timeline.id,
        author_id=owner.id,
        frame_number=12,
        frame_rate=24,
        time_seconds=0.5,
        body="Existing comment",
        annotation_json={"canvas_width": 100, "canvas_height": 100, "operations": []},
        status=CommentStatus.OPEN,
    )
    account = SocialAccount(
        user_id=owner.id,
        platform=SocialPlatform.YOUTUBE,
        platform_account_id=f"m12-{uuid.uuid4()}",
        display_name="M12 channel",
        encrypted_access_token="encrypted",
        scopes_json=[],
        profile_json={},
    )
    render = RenderJob(project_id=project.id, timeline_id=timeline.id, status=RenderStatus.COMPLETED, output_key="m12/render.mp4", output_format="mp4", forensic_metadata_json={})
    db_session.add_all([comment, account, render])
    db_session.flush()
    return project, timeline, comment, account, render


def _auth(user):
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _review_requests(graph, invitee):
    _, timeline, comment, _, _ = graph
    annotation = {"canvas_width": 100, "canvas_height": 100, "operations": []}
    return [
        ("get", f"{API}/timelines/{timeline.id}/review/comments?user_id={invitee.id}", None),
        ("post", f"{API}/timelines/{timeline.id}/review/comments", {"user_id": str(invitee.id), "frame_number": 24, "frame_rate": 24, "body": "New comment", "annotation": annotation}),
        ("patch", f"{API}/timelines/{timeline.id}/review/comments/{comment.id}", {"user_id": str(invitee.id), "status": "resolved"}),
        ("post", f"{API}/timelines/{timeline.id}/review/decision", {"user_id": str(invitee.id), "status": "approved", "note": "Approved"}),
        ("get", f"{API}/timelines/{timeline.id}/review/export?format=json&user_id={invitee.id}", None),
        ("post", f"{API}/timelines/{timeline.id}/review/participants", {"user_id": str(invitee.id), "participant_user_id": str(invitee.id), "role": "reviewer"}),
    ]


def _social_requests(graph, spoof_user):
    _, timeline, _, account, render = graph
    return [
        ("get", f"{API}/social/oauth/youtube/authorize?user_id={spoof_user.id}", None),
        ("get", f"{API}/social/accounts?user_id={spoof_user.id}", None),
        ("post", f"{API}/social/timelines/{timeline.id}/metadata?user_id={spoof_user.id}", None),
        ("post", f"{API}/social/timelines/{timeline.id}/publish", {"user_id": str(spoof_user.id), "social_account_id": str(account.id), "render_job_id": str(render.id), "title": "M12", "visibility": "private"}),
    ]


def _request(client, method, url, body, headers):
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    return getattr(client, method)(url, **kwargs)


def _patch_boundaries(monkeypatch):
    calls: list[str] = []
    store = _Redis()
    monkeypatch.setattr(social.redis, "from_url", lambda *args, **kwargs: store)
    monkeypatch.setattr(social, "make_pkce_pair", lambda: ("verifier", "challenge"))
    monkeypatch.setattr(social, "get_social_client", lambda _platform: _SocialClient())
    monkeypatch.setattr(social.generate_metadata_for_timeline, "delay", lambda *_args: calls.append("metadata") or _Task())
    monkeypatch.setattr(social.publish_timeline, "delay", lambda *_args: calls.append("publish") or _Task())
    return calls, store


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer malformed"}])
def test_m12_protected_endpoints_reject_anonymous_and_invalid_jwts(db_session, monkeypatch, headers):
    calls, store = _patch_boundaries(monkeypatch)
    owner, invitee = _user(db_session), _user(db_session)
    graph, client = _graph(db_session, owner), _client(db_session)
    for method, url, body in _review_requests(graph, invitee) + _social_requests(graph, invitee):
        response = _request(client, method, url, body, headers)
        assert response.status_code == 401, (method, url, response.text)
    assert calls == [] and store.values == {}


def test_m12_wrong_user_cannot_spoof_review_or_publishing_owner(db_session, monkeypatch):
    calls, store = _patch_boundaries(monkeypatch)
    owner, attacker, invitee = _user(db_session), _user(db_session), _user(db_session)
    graph, client = _graph(db_session, owner), _client(db_session)
    comment_count = db_session.query(ReviewComment).count()
    for method, url, body in _review_requests(graph, invitee):
        response = _request(client, method, url, body, _auth(attacker))
        assert response.status_code == 403, (method, url, response.text)
    for method, url, body in _social_requests(graph, owner)[1:]:
        response = _request(client, method, url, body, _auth(attacker))
        assert response.status_code in {200, 403}, (method, url, response.text)
        if method == "get":
            assert response.json() == []
        else:
            assert response.status_code == 403
    oauth = client.get(f"{API}/social/oauth/youtube/authorize?user_id={owner.id}", headers=_auth(attacker))
    assert oauth.status_code == 200
    saved = json.loads(next(iter(store.values.values())))
    assert saved["user_id"] == str(attacker.id)
    assert calls == []
    assert db_session.query(ReviewComment).count() == comment_count
    assert db_session.query(TimelineReview).count() == 0
    assert db_session.query(SocialPost).count() == 0


def test_m12_rightful_owner_can_use_every_review_and_social_endpoint(db_session, monkeypatch):
    calls, store = _patch_boundaries(monkeypatch)
    owner, invitee = _user(db_session), _user(db_session)
    graph, client = _graph(db_session, owner), _client(db_session)
    for method, url, body in _review_requests(graph, invitee) + _social_requests(graph, invitee):
        response = _request(client, method, url, body, _auth(owner))
        assert response.status_code in {200, 201, 202}, (method, url, response.status_code, response.text)
    assert calls == ["metadata", "publish"]
    assert db_session.query(TimelineReview).one().decided_by_id == owner.id
    assert db_session.query(SocialPost).one().social_account_id == graph[3].id
    saved = json.loads(next(iter(store.values.values())))
    assert saved["user_id"] == str(owner.id)


def test_m12_oauth_callback_remains_public_but_requires_one_time_server_state(db_session, monkeypatch):
    _calls, _store = _patch_boundaries(monkeypatch)
    response = _client(db_session).get(f"{API}/social/oauth/youtube/callback?code=code&state=missing")
    assert response.status_code == 400
    assert response.json()["detail"] == "OAuth state is invalid or expired"
