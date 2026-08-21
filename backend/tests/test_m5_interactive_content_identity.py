"""Real-JWT ownership coverage for the M5 interactive content route family."""
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
from app.models.entities import MediaAsset, MediaStatus, MediaType, Project, Timeline, User

API = "/api/v1"


def _load(name: str):
    path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_vantacut_m5_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


interactive = _load("interactive")
meme_gifs = _load("meme_gifs")
stickers = _load("stickers")
subtitles = _load("subtitles")
MODULES = (subtitles, stickers, meme_gifs)


class _QueuedTask:
    id = "m5-task"


def _client(db_session) -> TestClient:
    app = FastAPI()
    for module in MODULES:
        app.include_router(module.router, prefix=API)
    app.include_router(interactive.creator_router, prefix=API)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _user(db_session) -> User:
    user = User(email=f"m5-{uuid.uuid4().hex[:16]}@example.com", is_active=True)
    db_session.add(user); db_session.flush()
    return user


def _graph(db_session, owner: User):
    project = Project(owner_id=owner.id, name="M5 content identity")
    db_session.add(project); db_session.flush()
    asset = MediaAsset(
        project_id=project.id, filename="m5.mp4", storage_key="m5/video.mp4",
        proxy_key="m5/proxy.mp4", media_type=MediaType.VIDEO, status=MediaStatus.READY,
    )
    db_session.add(asset); db_session.flush()
    settings = {
        "subtitles": {
            "status": "completed",
            "items": [{"id": "cue-1", "start_time": 0, "end_time": 1, "text": "hello", "words": []}],
        },
        "bilingual_subtitles": {
            "status": "completed", "source_language": "zh", "target_language": "en",
            "bilingual_srt_key": "m5/bilingual.srt", "source_vtt_key": "m5/source.vtt",
            "target_vtt_key": "m5/target.vtt", "items": [],
        },
        "interactive_graph": {
            "schema_version": 1, "entry_node_id": "intro", "published": True,
            "nodes": [{"id": "intro", "title": "Intro", "media_asset_id": str(asset.id), "source_start": 0, "source_end": 1}],
            "edges": [],
        },
        "meme_gif": {"status": "completed", "events": []},
    }
    timeline = Timeline(project_id=project.id, name="M5 timeline", settings_json=settings)
    db_session.add(timeline); db_session.flush()
    return project, timeline, asset


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _graph_payload(asset: MediaAsset) -> dict[str, object]:
    return {
        "schema_version": 1, "entry_node_id": "intro", "published": False,
        "nodes": [{"id": "intro", "title": "Intro", "media_asset_id": str(asset.id), "source_start": 0, "source_end": 1}],
        "edges": [],
    }


def _requests(timeline: Timeline, asset: MediaAsset):
    legacy = {"user_id": str(timeline.project.owner_id)}
    return [
        ("post", f"{API}/timelines/{timeline.id}/generate-bilingual-subtitles", {**legacy, "target_language": "en"}),
        ("get", f"{API}/timelines/{timeline.id}/bilingual-subtitles/export?format=srt&track=bilingual&user_id={timeline.project.owner_id}", None),
        ("put", f"{API}/timelines/{timeline.id}/caption-style", {**legacy, "preset": "viral_yellow", "aspect_ratio": "9:16"}),
        ("post", f"{API}/timelines/{timeline.id}/recommend-stickers", {**legacy, "enabled": True}),
        ("put", f"{API}/timelines/{timeline.id}/ai-stickers/enabled", {**legacy, "enabled": False}),
        ("patch", f"{API}/timelines/{timeline.id}/stickers/sticker-1", {**legacy, "source": "user", "transform": {"x": .5, "y": .5, "scale": 1, "rotation": 0}}),
        ("post", f"{API}/timelines/{timeline.id}/generate-subtitles", {"source_asset_id": str(asset.id), "segments": [{"source_start": 0, "source_end": 1, "action": "keep", "confidence_score": 100, "reason": "keep"}]}),
        ("get", f"{API}/timelines/{timeline.id}/meme-gifs?user_id={timeline.project.owner_id}", None),
        ("post", f"{API}/timelines/{timeline.id}/meme-gifs", {**legacy, "source_asset_id": str(asset.id)}),
        ("get", f"{API}/timelines/{timeline.id}/interactive-analytics?user_id={timeline.project.owner_id}", None),
        ("put", f"{API}/timelines/{timeline.id}/interactive-graph", {**legacy, "graph": _graph_payload(asset)}),
    ]


def _patch_boundaries(monkeypatch, calls: list[str] | None = None) -> None:
    calls = calls if calls is not None else []
    for module, task_name in (
        (subtitles, "generate_bilingual_subtitles_for_timeline"),
        (subtitles, "generate_subtitles_for_timeline"),
        (meme_gifs, "generate_meme_gifs"),
    ):
        monkeypatch.setattr(getattr(module, task_name), "delay", lambda *args, _name=task_name, **kwargs: calls.append(_name) or _QueuedTask())
    monkeypatch.setattr(subtitles, "upload_bytes", lambda *args, **kwargs: calls.append("upload_bytes"))
    monkeypatch.setattr(subtitles, "create_download_url", lambda *args, **kwargs: "https://download.invalid/m5")
    monkeypatch.setattr(subtitles, "bilingual_to_ass", lambda *args, **kwargs: "m5-ass")
    monkeypatch.setattr(subtitles, "cues_to_ass", lambda *args, **kwargs: "m5-ass")
    monkeypatch.setattr(stickers, "recommend_stickers", lambda cues: [{"id": "sticker-1", "label": "hello", "fallback_emoji": "👋", "position": {"x": .5, "y": .5}, "scale": 1, "rotation": 0}])


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer malformed"}])
def test_m5_endpoints_reject_anonymous_and_invalid_jwts(db_session, monkeypatch, headers):
    _patch_boundaries(monkeypatch); owner = _user(db_session); _, timeline, asset = _graph(db_session, owner); client = _client(db_session)
    for method, url, body in _requests(timeline, asset):
        kwargs = {"headers": headers}
        if body is not None:
            kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code == 401, (method, url, response.text)


def test_m5_wrong_user_and_legacy_spoof_cannot_mutate_or_trigger_side_effects(db_session, monkeypatch):
    calls: list[str] = []; _patch_boundaries(monkeypatch, calls)
    owner, attacker = _user(db_session), _user(db_session); _, timeline, asset = _graph(db_session, owner); client = _client(db_session)
    original = copy.deepcopy(timeline.settings_json)
    for method, url, body in _requests(timeline, asset):
        kwargs = {"headers": _auth(attacker)}
        if body is not None:
            kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code == 403, (method, url, response.text)
    assert calls == []
    db_session.refresh(timeline)
    assert timeline.settings_json == original


def test_m5_rightful_owner_can_use_every_migrated_endpoint(db_session, monkeypatch):
    _patch_boundaries(monkeypatch); owner = _user(db_session); _, timeline, asset = _graph(db_session, owner); client = _client(db_session)
    for method, url, body in _requests(timeline, asset):
        kwargs = {"headers": _auth(owner)}
        if body is not None:
            kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code in {200, 202}, (method, url, response.status_code, response.text)


def test_m5_public_interactive_player_and_sticker_library_remain_separate(db_session, monkeypatch):
    owner = _user(db_session); _, timeline, _ = _graph(db_session, owner)
    app = FastAPI(); app.include_router(interactive.player_router, prefix=API); app.include_router(stickers.library_router, prefix=API)
    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(interactive, "create_download_url", lambda *args, **kwargs: "https://download.invalid/proxy")
    monkeypatch.setattr(stickers, "animated_sticker_webp", lambda sticker_id: b"webp")
    client = TestClient(app)
    assert client.get(f"{API}/interactive/timelines/{timeline.id}/manifest").status_code == 200
    assert client.get(f"{API}/sticker-library/wave.webp").status_code == 200
