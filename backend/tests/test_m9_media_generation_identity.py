"""Real-JWT ownership coverage for M9 media-generation routes."""
from __future__ import annotations

import copy
import importlib.util
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db.session import get_db
from app.models.entities import MediaAsset, MediaStatus, MediaType, Project, RenderJob, RenderStatus, Timeline, User

API = "/api/v1"
NAMES = ("forensics", "matting", "mechanical_ar", "profanity", "semantic_stock_broll", "video_generation")


def _load(name: str):
    path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_vantacut_m9_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


forensics, matting, mechanical, profanity, stock, video = map(_load, NAMES)
MODULES = (forensics, matting, mechanical, profanity, stock)


class _Task:
    id = "m9-task"


def _client(db_session) -> TestClient:
    app = FastAPI()
    for module in MODULES:
        app.include_router(module.router, prefix=API)
    app.include_router(video.broll_router, prefix=API)
    app.include_router(video.outpaint_router, prefix=API)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _user(db_session) -> User:
    user = User(email=f"m9-{uuid.uuid4().hex[:16]}@example.com", is_active=True)
    db_session.add(user); db_session.flush()
    return user


def _graph(db_session, owner: User):
    project = Project(owner_id=owner.id, name="M9 media generation")
    db_session.add(project); db_session.flush()
    timeline = Timeline(project_id=project.id, name="M9 current", version=1, is_current=True, settings_json={"subtitles": {"status": "completed"}})
    asset = MediaAsset(project_id=project.id, filename="m9.mp4", storage_key="m9/video.mp4", proxy_key="m9/proxy.mp4", media_type=MediaType.VIDEO, status=MediaStatus.READY, duration_seconds=120)
    db_session.add_all([timeline, asset]); db_session.flush()
    render = RenderJob(project_id=project.id, timeline_id=timeline.id, status=RenderStatus.COMPLETED, output_key="m9/render.mp4", output_format="mp4", forensic_metadata_json={})
    db_session.add(render); db_session.flush()
    return project, timeline, asset, render


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _requests(graph):
    project, timeline, asset, render = graph
    spoof = {"user_id": str(project.owner_id)}
    return [
        ("post", f"{API}/renders/{render.id}/forensics/verify", {"json": spoof}),
        ("post", f"{API}/media/{asset.id}/matting", {"json": {**spoof, "mode": "click", "frame_time": 1, "points": [{"x": .5, "y": .5, "positive": True}]}}),
        ("post", f"{API}/timelines/{timeline.id}/mechanical-ar/code", {"data": spoof, "files": {"file": ("program.py", b"print('safe')", "text/x-python")}}),
        ("post", f"{API}/timelines/{timeline.id}/mechanical-ar/analyze", {"json": {**spoof, "media_asset_id": str(asset.id)}}),
        ("post", f"{API}/timelines/{timeline.id}/profanity-filter", {"json": {**spoof, "sfx_style": "beep", "emoji_style": "angry"}}),
        ("post", f"{API}/timelines/{timeline.id}/b-roll/semantic-stock", {"json": {**spoof, "source_asset_id": str(asset.id)}}),
        ("get", f"{API}/timelines/{timeline.id}/b-roll/semantic-stock?user_id={project.owner_id}", {}),
        ("post", f"{API}/timelines/{timeline.id}/b-roll/generate", {"json": {**spoof, "source_asset_id": str(asset.id)}}),
        ("post", f"{API}/video/outpaint", {"json": {**spoof, "media_asset_id": str(asset.id), "end_time": 2}}),
    ]


def _patch_boundaries(monkeypatch, calls: list[str] | None = None):
    calls = calls if calls is not None else []
    for module, name in (
        (matting, "generate_video_matte"), (mechanical, "analyze_mechanical_timeline"),
        (profanity, "apply_profanity_filter"), (stock, "generate_semantic_stock_broll"),
        (video, "generate_broll"), (video, "outpaint_video"),
    ):
        monkeypatch.setattr(getattr(module, name), "delay", lambda *args, _name=name, **kwargs: calls.append(_name) or _Task())
    monkeypatch.setattr(mechanical, "upload_bytes", lambda *args, **kwargs: calls.append("upload_bytes"))
    monkeypatch.setattr(forensics, "download_object", lambda *args, **kwargs: calls.append("download_object"))
    monkeypatch.setattr(forensics, "extract_forensic_watermark", lambda *args, **kwargs: {"detected": True})
    monkeypatch.setattr(forensics, "verify_c2pa_asset", lambda *args, **kwargs: {"available": True})


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer malformed"}])
def test_m9_endpoints_reject_anonymous_and_invalid_jwts(db_session, monkeypatch, headers):
    _patch_boundaries(monkeypatch); owner = _user(db_session); graph = _graph(db_session, owner); client = _client(db_session)
    for method, url, kwargs in _requests(graph):
        response = getattr(client, method)(url, headers=headers, **kwargs)
        assert response.status_code == 401, (method, url, response.text)


def test_m9_wrong_user_and_legacy_spoof_cannot_mutate_or_trigger_side_effects(db_session, monkeypatch):
    calls: list[str] = []; _patch_boundaries(monkeypatch, calls)
    owner, attacker = _user(db_session), _user(db_session); graph = _graph(db_session, owner); client = _client(db_session)
    original = copy.deepcopy(graph[1].settings_json)
    for method, url, kwargs in _requests(graph):
        response = getattr(client, method)(url, headers=_auth(attacker), **kwargs)
        assert response.status_code == 403, (method, url, response.text)
    assert calls == []
    db_session.refresh(graph[1]); assert graph[1].settings_json == original


def test_m9_rightful_owner_can_use_every_migrated_endpoint(db_session, monkeypatch):
    _patch_boundaries(monkeypatch); owner = _user(db_session); graph = _graph(db_session, owner); client = _client(db_session)
    for method, url, kwargs in _requests(graph):
        response = getattr(client, method)(url, headers=_auth(owner), **kwargs)
        assert response.status_code in {200, 201, 202}, (method, url, response.status_code, response.text)
