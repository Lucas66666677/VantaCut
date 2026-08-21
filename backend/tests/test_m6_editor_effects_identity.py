"""Real-JWT ownership coverage for the M6 editor-effects route family."""
from __future__ import annotations

import copy
import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db.session import get_db
from app.models.entities import MediaAsset, MediaStatus, MediaType, Project, Timeline, User

API = "/api/v1"
NAMES = ("color_filters", "data_charts", "keyframes", "nudge", "speed_curves", "transitions", "visual_hooks")


def _load(name: str):
    path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_vantacut_m6_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


color_filters, data_charts, keyframes, nudge, speed_curves, transitions, visual_hooks = map(_load, NAMES)
MODULES = (color_filters, data_charts, keyframes, nudge, speed_curves, transitions, visual_hooks)


class _QueuedTask:
    id = "m6-task"


def _client(db_session) -> TestClient:
    app = FastAPI()
    for module in MODULES:
        app.include_router(module.router, prefix=API)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _user(db_session) -> User:
    user = User(email=f"m6-{uuid.uuid4().hex[:16]}@example.com", is_active=True)
    db_session.add(user); db_session.flush()
    return user


def _graph(db_session, owner: User):
    project = Project(owner_id=owner.id, name="M6 editor effects")
    db_session.add(project); db_session.flush()
    video = MediaAsset(project_id=project.id, filename="m6.mp4", storage_key="m6/video.mp4", proxy_key="m6/proxy.mp4", media_type=MediaType.VIDEO, status=MediaStatus.READY, duration_seconds=10)
    image = MediaAsset(project_id=project.id, filename="m6.jpg", storage_key="m6/image.jpg", media_type=MediaType.IMAGE, status=MediaStatus.READY)
    db_session.add_all([video, image]); db_session.flush()
    timeline = Timeline(project_id=project.id, name="M6 timeline", settings_json={
        "confirmed_timeline": {"source_asset_id": str(video.id), "tracks": [{"id": "main", "type": "main_video", "clips": [{"source_start": 0, "source_end": 10, "action": "keep"}]}]},
    })
    db_session.add(timeline); db_session.flush()
    return project, timeline, video, image


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _requests(timeline: Timeline, video: MediaAsset, image: MediaAsset):
    owner_id = str(timeline.project.owner_id)
    now = datetime.now(UTC)
    transition = {"id": "fade-1", "from_clip_id": str(uuid.uuid4()), "to_clip_id": str(uuid.uuid4()), "kind": "crossfade", "duration_seconds": .35}
    return [
        ("put", f"{API}/timelines/{timeline.id}/color-filter", {"user_id": owner_id, "preset_id": "clean", "intensity": 70}),
        ("post", f"{API}/timelines/{timeline.id}/color-match", {"user_id": owner_id, "reference_image_asset_id": str(image.id), "source_asset_id": str(video.id)}),
        ("post", f"{API}/timelines/{timeline.id}/data-charts", {"user_id": owner_id, "points": [{"timestamp": now.isoformat(), "value": 1}, {"timestamp": (now + timedelta(seconds=1)).isoformat(), "value": 2}], "start_time": 0, "end_time": 1}),
        ("put", f"{API}/timelines/{timeline.id}/keyframes", {"user_id": owner_id, "animations": [{"clip_id": str(uuid.uuid4()), "keyframes": [{"time": 0, "value": {}}, {"time": 1, "value": {"x": .6}}]}]}),
        ("post", f"{API}/timelines/{timeline.id}/nudge", {"user_id": owner_id, "instruction": "make it brighter", "target_clip_ids": ["clip-1"]}),
        ("put", f"{API}/timelines/{timeline.id}/speed-curves", {"user_id": owner_id, "curves": [{"clip_id": str(uuid.uuid4()), "points": [{"position": 0, "speed": 1}, {"position": 1, "speed": 2}]}]}),
        ("put", f"{API}/timelines/{timeline.id}/transitions", {"user_id": owner_id, "transitions": [transition]}),
        ("post", f"{API}/timelines/{timeline.id}/transitions/fade-1/build?user_id={owner_id}", None),
        ("put", f"{API}/timelines/{timeline.id}/visual-hooks", {"user_id": owner_id, "enabled": True, "style": "gradient_line", "platform": "tiktok"}),
    ]


def _patch_boundaries(monkeypatch, calls: list[str] | None = None) -> None:
    calls = calls if calls is not None else []
    monkeypatch.setattr(color_filters, "get_preset_lut", lambda preset_id: type("Preset", (), {"id": preset_id, "name": "Clean"})())
    monkeypatch.setattr(color_filters, "preset_lut_cube", lambda preset_id: "cube")
    monkeypatch.setattr(color_filters, "upload_bytes", lambda *args, **kwargs: calls.append("upload_bytes"))
    monkeypatch.setattr(color_filters, "download_object", lambda *args, **kwargs: calls.append("download_object"))
    monkeypatch.setattr(color_filters, "extract_video_reference_frame", lambda *args, **kwargs: calls.append("extract_frame"))
    monkeypatch.setattr(color_filters, "extract_color_match", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(color_filters, "generate_color_match_cube", lambda *args, **kwargs: calls.append("generate_cube"))
    monkeypatch.setattr(color_filters, "write_style_profile", lambda *args, **kwargs: calls.append("write_profile"))
    monkeypatch.setattr(color_filters, "upload_object", lambda *args, **kwargs: calls.append("upload_object"))
    monkeypatch.setattr(color_filters, "create_download_url", lambda *args, **kwargs: "https://download.invalid/m6")
    monkeypatch.setattr(data_charts.generate_chart, "delay", lambda *args, **kwargs: calls.append("generate_chart") or _QueuedTask())
    monkeypatch.setattr(transitions.build_transition_asset, "delay", lambda *args, **kwargs: calls.append("build_transition") or _QueuedTask())
    monkeypatch.setattr(nudge, "text_provider_dependency", lambda: calls.append("text_provider") or object())
    monkeypatch.setattr(nudge, "plan_nudge", lambda *args, **kwargs: ([], "No change", "test"))
    monkeypatch.setattr(visual_hooks, "analyze_opening_hook", lambda *args, **kwargs: {"highlight_candidate": {"timeline_start": 6}})


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer malformed"}])
def test_m6_endpoints_reject_anonymous_and_invalid_jwts(db_session, monkeypatch, headers):
    _patch_boundaries(monkeypatch); owner = _user(db_session); _, timeline, video, image = _graph(db_session, owner); client = _client(db_session)
    for method, url, body in _requests(timeline, video, image):
        kwargs = {"headers": headers}
        if body is not None:
            kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code == 401, (method, url, response.text)


def test_m6_wrong_user_and_legacy_spoof_cannot_mutate_or_trigger_side_effects(db_session, monkeypatch):
    calls: list[str] = []; _patch_boundaries(monkeypatch, calls)
    owner, attacker = _user(db_session), _user(db_session); _, timeline, video, image = _graph(db_session, owner); client = _client(db_session)
    original = copy.deepcopy(timeline.settings_json)
    for method, url, body in _requests(timeline, video, image):
        kwargs = {"headers": _auth(attacker)}
        if body is not None:
            kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code == 403, (method, url, response.text)
    assert calls == []
    db_session.refresh(timeline)
    assert timeline.settings_json == original


def test_m6_rightful_owner_can_use_every_migrated_endpoint(db_session, monkeypatch):
    _patch_boundaries(monkeypatch); owner = _user(db_session); _, timeline, video, image = _graph(db_session, owner); client = _client(db_session)
    for method, url, body in _requests(timeline, video, image):
        kwargs = {"headers": _auth(owner)}
        if body is not None:
            kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code in {200, 202}, (method, url, response.status_code, response.text)


def test_m6_color_filter_catalog_remains_intentionally_public(db_session):
    app = FastAPI(); app.include_router(color_filters.router, prefix=API)
    app.dependency_overrides[get_db] = lambda: iter([db_session])
    client = TestClient(app)
    assert client.get(f"{API}/timelines/color-filter-presets").status_code == 200
