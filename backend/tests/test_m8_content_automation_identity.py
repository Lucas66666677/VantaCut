"""Real-JWT ownership coverage for M8 content-automation routes."""
from __future__ import annotations

import copy
import importlib.util
import uuid
from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db.session import get_db
from app.models.entities import (
    AvatarProfile, MediaAsset, MediaStatus, MediaType, Project, Timeline, User,
    VoiceProfile, VoiceProfileStatus,
)

API = "/api/v1"
NAMES = ("finance", "fitness_overlay", "lecturas", "localization", "long_to_shorts", "talking_head", "vertical_dual_layout")


def _load(name: str):
    path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_vantacut_m8_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


finance, fitness, lecturas, localization, shorts, talking, vertical = map(_load, NAMES)
MODULES = (finance, fitness, lecturas, localization, shorts, talking, vertical)


class _Task:
    id = "m8-task"


def _client(db_session) -> TestClient:
    app = FastAPI()
    for module in MODULES:
        app.include_router(module.router, prefix=API)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _user(db_session) -> User:
    user = User(email=f"m8-{uuid.uuid4().hex[:16]}@example.com", is_active=True, render_credits=6)
    db_session.add(user); db_session.flush()
    return user


def _graph(db_session, owner: User):
    project = Project(owner_id=owner.id, name="M8 content automation")
    db_session.add(project); db_session.flush()
    timeline = Timeline(
        project_id=project.id, name="M8 current", version=1, is_current=True,
        settings_json={"finance_tracks": [{"id": "track", "status": "completed"}], "long_to_shorts": {"status": "completed", "shorts": [{}, {}, {}]}},
    )
    asset = MediaAsset(project_id=project.id, filename="m8.mp4", storage_key="m8/video.mp4", proxy_key="m8/proxy.mp4", media_type=MediaType.VIDEO, status=MediaStatus.READY, duration_seconds=120)
    db_session.add_all([timeline, asset]); db_session.flush()
    avatar = AvatarProfile(owner_id=owner.id, project_id=project.id, name="M8 avatar", renderer="unreal_mrq", status="ready", asset_bundle_key="m8/avatar.zip", rig_mapping_json={})
    voice = VoiceProfile(project_id=project.id, created_by_id=owner.id, source_media_asset_id=asset.id, name="M8 voice", provider_name="test", status=VoiceProfileStatus.READY, metadata_json={})
    db_session.add_all([avatar, voice]); db_session.flush()
    return project, timeline, asset, avatar, voice


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _requests(graph):
    project, timeline, asset, avatar, voice = graph
    spoof = {"user_id": str(project.owner_id)}
    return [
        ("post", f"{API}/timelines/{timeline.id}/finance-tracks", {**spoof, "symbol": "2330.TW", "history_start": str(date(2024, 1, 1)), "history_end": str(date(2024, 2, 1)), "start_time": 0, "end_time": 10}),
        ("get", f"{API}/timelines/{timeline.id}/finance-tracks?user_id={project.owner_id}", None),
        ("patch", f"{API}/timelines/{timeline.id}/finance-tracks/track", {**spoof, "annotations": []}),
        ("post", f"{API}/timelines/{timeline.id}/fitness-overlay", {**spoof, "source_asset_id": str(asset.id)}),
        ("get", f"{API}/timelines/{timeline.id}/fitness-overlay?user_id={project.owner_id}", None),
        ("post", f"{API}/timelines/{timeline.id}/lecturas", {**spoof, "avatar_profile_id": str(avatar.id), "source_asset_id": str(asset.id), "confirm_digital_avatar_disclosure": True}),
        ("post", f"{API}/timelines/{timeline.id}/localized-dubs", {**spoof, "render_job_id": str(uuid.uuid4()), "voice_profile_id": str(voice.id), "target_language": "en", "consent_confirmed": True}),
        ("post", f"{API}/timelines/{timeline.id}/long-to-shorts/export", {**spoof, "resolution": "1080p"}),
        ("post", f"{API}/timelines/{timeline.id}/long-to-shorts", {**spoof, "source_media_asset_id": str(asset.id)}),
        ("get", f"{API}/timelines/{timeline.id}/long-to-shorts?user_id={project.owner_id}", None),
        ("post", f"{API}/timelines/{timeline.id}/talking-head-confidence", {**spoof, "source_asset_id": str(asset.id)}),
        ("get", f"{API}/timelines/{timeline.id}/talking-head-confidence?user_id={project.owner_id}", None),
        ("post", f"{API}/timelines/{timeline.id}/vertical-dual-layout", {**spoof, "source_asset_id": str(asset.id)}),
    ]


def _patch_tasks(monkeypatch, calls: list[str] | None = None):
    calls = calls if calls is not None else []
    for module, name in (
        (finance, "refresh_finance_track"), (fitness, "analyze_fitness_reps"),
        (lecturas, "generate_lecturas_interventions"), (localization, "generate_dubbed_version"),
        (shorts, "generate_long_to_shorts"), (shorts, "export_long_to_shorts_batch"),
        (talking, "analyze_speaker_state"), (vertical, "analyze_vertical_dual_layout_task"),
    ):
        monkeypatch.setattr(getattr(module, name), "delay", lambda *args, _name=name, **kwargs: calls.append(_name) or _Task())


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer malformed"}])
def test_m8_endpoints_reject_anonymous_and_invalid_jwts(db_session, monkeypatch, headers):
    _patch_tasks(monkeypatch); owner = _user(db_session); graph = _graph(db_session, owner); client = _client(db_session)
    for method, url, body in _requests(graph):
        kwargs = {"headers": headers}
        if body is not None: kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code == 401, (method, url, response.text)


def test_m8_wrong_user_and_legacy_spoof_cannot_mutate_or_enqueue(db_session, monkeypatch):
    calls: list[str] = []; _patch_tasks(monkeypatch, calls)
    owner, attacker = _user(db_session), _user(db_session); graph = _graph(db_session, owner); client = _client(db_session)
    original = copy.deepcopy(graph[1].settings_json)
    for method, url, body in _requests(graph):
        kwargs = {"headers": _auth(attacker)}
        if body is not None: kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code == 403, (method, url, response.text)
    assert calls == []
    db_session.refresh(graph[1]); assert graph[1].settings_json == original


def test_m8_rightful_owner_can_use_every_migrated_endpoint(db_session, monkeypatch):
    _patch_tasks(monkeypatch); owner = _user(db_session); graph = _graph(db_session, owner); client = _client(db_session)
    for method, url, body in _requests(graph):
        kwargs = {"headers": _auth(owner)}
        if body is not None: kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code in {200, 202}, (method, url, response.status_code, response.text)
