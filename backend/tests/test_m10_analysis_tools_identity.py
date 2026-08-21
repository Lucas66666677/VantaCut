"""Real-JWT ownership coverage for M10 analysis and one-click tools."""
from __future__ import annotations

import copy
import importlib.util
import sys
import types
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db.session import get_db
from app.models.entities import AIFeedback, Clip, MediaAsset, MediaStatus, MediaType, Project, Template, Timeline, TrackType, User

API = "/api/v1"

# The production image includes torch. Auth CI intentionally has a minimal
# dependency set, so substitute only the model-inference boundary at import.
retention_boundary = types.ModuleType("app.services.retention_prediction")
retention_boundary.predict_timeline_retention = lambda db, timeline: {
    "model_name": "test", "prediction_mode": "heuristic_baseline", "is_calibrated": False,
    "window_seconds": 5, "curve": [], "hotspots": [], "summary": "test",
}
sys.modules[retention_boundary.__name__] = retention_boundary


def _load(name: str):
    path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_vantacut_m10_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


analysis, one_click, snapping = map(_load, ("analysis", "one_click", "semantic_snapping"))
MODULES = (analysis, one_click, snapping)


class _Task:
    id = "m10-task"


def _client(db_session) -> TestClient:
    app = FastAPI()
    for module in MODULES:
        app.include_router(module.router, prefix=API)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _user(db_session) -> User:
    user = User(email=f"m10-{uuid.uuid4().hex[:16]}@example.com", is_active=True)
    db_session.add(user); db_session.flush()
    return user


def _graph(db_session, owner: User):
    project = Project(owner_id=owner.id, name="M10 analysis tools")
    db_session.add(project); db_session.flush()
    asset = MediaAsset(project_id=project.id, filename="m10.mp4", storage_key="m10/video.mp4", proxy_key="m10/proxy.mp4", media_type=MediaType.VIDEO, status=MediaStatus.READY, duration_seconds=12, metadata_json={"visual_motion": .5})
    db_session.add(asset); db_session.flush()
    timeline = Timeline(project_id=project.id, name="M10 current", version=1, is_current=True, settings_json={})
    db_session.add(timeline); db_session.flush()
    clip = Clip(timeline_id=timeline.id, source_asset_id=asset.id, source_start=0, source_end=10, track=TrackType.MAIN_VIDEO, order_index=0, z_index=0, enabled=True)
    template = Template(project_id=project.id, source_asset_id=asset.id, name="M10 template", description="test", structure_json={
        "template_name": "M10 template", "summary": "test", "aspect_ratio": "16:9", "overall_pacing": "medium",
        "scenes": [{"start": 0, "end": 1, "shot_type": "wide", "purpose": "opening", "pace": "medium", "dialogue_prompt": "hello", "filming_instruction": "hold"}],
    })
    db_session.add_all([clip, template]); db_session.flush()
    return project, timeline, asset, clip, template


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _requests(graph):
    project, timeline, asset, clip, template = graph
    spoof = {"user_id": str(project.owner_id)}
    return [
        ("post", f"{API}/analysis/timelines/{timeline.id}/hook-check", {**spoof}),
        ("post", f"{API}/analysis/timelines/{timeline.id}/retention-prediction", {**spoof, "refresh": True}),
        ("post", f"{API}/analysis/language-review", {**spoof, "media_asset_id": str(asset.id), "timeline_id": str(timeline.id)}),
        ("post", f"{API}/analysis/gaming-highlights", {"media_asset_id": str(asset.id)}),
        ("post", f"{API}/analysis/feedback", {**spoof, "timeline_id": str(timeline.id), "clip_id": str(clip.id), "original_ai_decision": "remove", "user_final_decision": "keep"}),
        ("post", f"{API}/analysis/extract-template", {"media_asset_id": str(asset.id)}),
        ("post", f"{API}/projects/{project.id}/one-click/generate", {**spoof, "template_id": "punchy-vlog-15s", "media_asset_ids": [str(asset.id)]}),
        ("get", f"{API}/timelines/{timeline.id}/semantic-snap-points?user_id={project.owner_id}", None),
        ("post", f"{API}/analysis/timelines/{timeline.id}/hook-rescue", {**spoof}),
    ]


def _patch_boundaries(monkeypatch, graph, calls: list[str] | None = None):
    calls = calls if calls is not None else []
    for module, name in ((analysis, "review_language_video"), (analysis, "generate_gaming_highlights"), (one_click, "generate_one_click_video")):
        monkeypatch.setattr(getattr(module, name), "delay", lambda *args, _name=name, **kwargs: calls.append(_name) or _Task())
    monkeypatch.setattr(analysis, "predict_timeline_retention", lambda *args, **kwargs: calls.append("retention_model") or {
        "model_name": "test", "prediction_mode": "heuristic_baseline", "is_calibrated": False,
        "window_seconds": 5, "curve": [], "hotspots": [], "summary": "test",
    })
    monkeypatch.setattr(analysis, "extract_template", lambda *args, **kwargs: calls.append("template_provider") or graph[4])


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer malformed"}])
def test_m10_endpoints_reject_anonymous_and_invalid_jwts(db_session, monkeypatch, headers):
    owner = _user(db_session); graph = _graph(db_session, owner); _patch_boundaries(monkeypatch, graph); client = _client(db_session)
    for method, url, body in _requests(graph):
        kwargs = {"headers": headers}
        if body is not None: kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code == 401, (method, url, response.text)


def test_m10_wrong_user_and_legacy_spoof_cannot_mutate_or_trigger_side_effects(db_session, monkeypatch):
    calls: list[str] = []; owner, attacker = _user(db_session), _user(db_session); graph = _graph(db_session, owner); _patch_boundaries(monkeypatch, graph, calls); client = _client(db_session)
    original = copy.deepcopy(graph[1].settings_json); feedback_count = db_session.query(AIFeedback).count()
    for method, url, body in _requests(graph):
        kwargs = {"headers": _auth(attacker)}
        if body is not None: kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code == 403, (method, url, response.text)
    assert calls == [] and db_session.query(AIFeedback).count() == feedback_count
    db_session.refresh(graph[1]); assert graph[1].settings_json == original


def test_m10_rightful_owner_can_use_every_migrated_endpoint(db_session, monkeypatch):
    owner = _user(db_session); graph = _graph(db_session, owner); _patch_boundaries(monkeypatch, graph); client = _client(db_session)
    for method, url, body in _requests(graph):
        kwargs = {"headers": _auth(owner)}
        if body is not None: kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code in {200, 201, 202}, (method, url, response.status_code, response.text)
