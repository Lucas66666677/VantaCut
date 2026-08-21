"""Real-JWT ownership coverage for the M4 audio and voice route family."""
from __future__ import annotations

import importlib.util
import copy
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db.session import get_db
from app.models.entities import MediaAsset, MediaStatus, MediaType, Project, Timeline, User, VoiceProfile, VoiceProfileStatus

API = "/api/v1"


def _load(name: str):
    path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_vantacut_m4_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audio_delivery = _load("audio_delivery")
audio_sync = _load("audio_sync")
narrations = _load("narrations")
smart_audio_remix = _load("smart_audio_remix")
speaker = _load("speaker")
text_to_music = _load("text_to_music")
voice = _load("voice")
MODULES = (audio_delivery, audio_sync, narrations, smart_audio_remix, speaker, text_to_music, voice)


class _QueuedTask:
    id = "m4-task"


def _client(db_session) -> TestClient:
    app = FastAPI()
    for module in MODULES:
        app.include_router(module.router, prefix=API)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _user(db_session) -> User:
    row = User(email=f"m4-{uuid.uuid4().hex[:16]}@example.com", is_active=True)
    db_session.add(row); db_session.flush()
    return row


def _graph(db_session, owner: User):
    project = Project(owner_id=owner.id, name="M4 audio identity")
    db_session.add(project); db_session.flush()
    settings = {"confirmed_timeline": {"tracks": [{"id": "main", "type": "main_video", "clips": [{"source_start": 0, "source_end": 4, "action": "keep"}]}]}, "subtitles": {"items": [{"id": "cue-1"}]}}
    timeline = Timeline(project_id=project.id, name="M4 timeline", settings_json=settings)
    video = MediaAsset(project_id=project.id, filename="m4.mp4", storage_key="m4/video.mp4", audio_key="m4/video.wav", media_type=MediaType.VIDEO, status=MediaStatus.READY, metadata_json={"stems": {"status": "completed"}})
    external = MediaAsset(project_id=project.id, filename="m4.wav", storage_key="m4/external.wav", audio_key="m4/external.wav", media_type=MediaType.AUDIO, status=MediaStatus.READY)
    db_session.add_all([timeline, video, external]); db_session.flush()
    profile = VoiceProfile(project_id=project.id, created_by_id=owner.id, source_media_asset_id=video.id, name="M4 speaker", provider_name="test", status=VoiceProfileStatus.READY, metadata_json={})
    db_session.add(profile); db_session.flush()
    return project, timeline, video, external, profile


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _requests(project, timeline, video, external, profile):
    return [
        ("post", f"{API}/media/{video.id}/extract-stems", {"model_name": "htdemucs"}),
        ("put", f"{API}/timelines/{timeline.id}/stem-mix", {"source_asset_id": str(video.id)}),
        ("post", f"{API}/timelines/{timeline.id}/generate-soundscape", {"layout": "5.1"}),
        ("post", f"{API}/timelines/{timeline.id}/audio-sync", {"video_asset_id": str(video.id), "external_audio_asset_id": str(external.id)}),
        ("get", f"{API}/timelines/{timeline.id}/audio-sync", None),
        ("post", f"{API}/timelines/{timeline.id}/narrations", {"text": "M4 narration"}),
        ("post", f"{API}/timelines/{timeline.id}/smart-audio-remix", {"bgm_asset_id": str(external.id), "target_duration_seconds": 4}),
        ("get", f"{API}/timelines/{timeline.id}/smart-audio-remix", None),
        ("delete", f"{API}/timelines/{timeline.id}/smart-audio-remix", None),
        ("post", f"{API}/media/{video.id}/analyze-speaker-state", {}),
        ("post", f"{API}/media/{video.id}/redirect-gaze", {"confirm_consent": True}),
        ("post", f"{API}/timelines/{timeline.id}/generated-music", {"prompt": "calm cinematic piano"}),
        ("get", f"{API}/timelines/{timeline.id}/generated-music", None),
        ("post", f"{API}/projects/{project.id}/voice-profiles", {"source_media_asset_id": str(video.id), "name": "M4 voice", "consent_confirmed": True}),
        ("get", f"{API}/projects/{project.id}/voice-profiles", None),
        ("post", f"{API}/timelines/{timeline.id}/voice-replacements", {"voice_profile_id": str(profile.id), "cue_id": "cue-1", "replacement_text": "M4 replacement", "consent_confirmed": True}),
        ("post", f"{API}/timelines/{timeline.id}/voice-morphs", {"source_media_asset_id": str(video.id), "source_start": 0, "source_end": 1, "timeline_start": 0, "character_id": "robot", "consent_confirmed": True}),
    ]


def _patch_tasks(monkeypatch):
    for module, name in ((audio_delivery, "extract_stems"), (audio_delivery, "generate_soundscape_for_timeline"), (audio_sync, "align_external_audio"), (narrations, "generate_tts_narration"), (smart_audio_remix, "generate_smart_audio_remix"), (speaker, "analyze_speaker_state"), (speaker, "redirect_gaze"), (text_to_music, "generate_timeline_music"), (voice, "extract_voice_profile"), (voice, "generate_voice_replacement"), (voice, "generate_voice_morph")):
        monkeypatch.setattr(getattr(module, name), "delay", lambda *args, **kwargs: _QueuedTask())
    monkeypatch.setattr(voice, "get_voice_clone_provider", lambda: type("Provider", (), {"name": "test"})())


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer malformed"}])
def test_m4_endpoints_reject_anonymous_and_invalid_jwts(db_session, monkeypatch, headers):
    _patch_tasks(monkeypatch); owner = _user(db_session); graph = _graph(db_session, owner); client = _client(db_session)
    for method, url, body in _requests(*graph):
        kwargs = {"headers": headers}
        if body is not None:
            kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code == 401, (method, url, response.text)


def test_m4_wrong_user_and_legacy_spoofed_identity_cannot_queue_or_mutate(db_session, monkeypatch):
    _patch_tasks(monkeypatch); owner, attacker = _user(db_session), _user(db_session); graph = _graph(db_session, owner); client = _client(db_session)
    calls: list[str] = []
    for module, name in ((audio_delivery, "extract_stems"), (audio_delivery, "generate_soundscape_for_timeline"), (audio_sync, "align_external_audio"), (narrations, "generate_tts_narration"), (smart_audio_remix, "generate_smart_audio_remix"), (speaker, "analyze_speaker_state"), (speaker, "redirect_gaze"), (text_to_music, "generate_timeline_music"), (voice, "extract_voice_profile"), (voice, "generate_voice_replacement"), (voice, "generate_voice_morph")):
        monkeypatch.setattr(getattr(module, name), "delay", lambda *args, _name=name, **kwargs: calls.append(_name) or _QueuedTask())
    project, timeline, video, external, profile = graph
    original_settings = copy.deepcopy(timeline.settings_json)
    for method, url, body in _requests(*graph):
        spoofed = {**(body or {}), "user_id": str(owner.id)}
        response = getattr(client, method)(url, json=spoofed, headers=_auth(attacker))
        assert response.status_code == 403, (method, url, response.text)
    assert calls == []
    db_session.refresh(timeline)
    assert timeline.settings_json == original_settings


def test_m4_rightful_owner_can_use_every_migrated_endpoint(db_session, monkeypatch):
    _patch_tasks(monkeypatch); owner = _user(db_session); graph = _graph(db_session, owner); client = _client(db_session)
    for method, url, body in _requests(*graph):
        kwargs = {"headers": _auth(owner)}
        if body is not None:
            kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code in {200, 202}, (method, url, response.status_code, response.text)
    created = db_session.query(VoiceProfile).filter_by(project_id=graph[0].id, name="M4 voice").one()
    assert created.created_by_id == owner.id
