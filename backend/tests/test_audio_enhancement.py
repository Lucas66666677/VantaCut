"""Batch 2A security tests for app/api/v1/audio_enhancement.py (5 routes:
noise-reduction and studio-sound CRUD on /{timeline_id}/clips/{clip_id}).

Mocks ONLY the Celery task boundary (enhance_audio.delay /
enhance_studio_sound.delay). Identity (get_current_user) and authorization
(`_authorize_timeline_clip`, including the timeline<->clip cross-check) run
for real against the test database.

Parametrized across all 5 route/method combinations rather than duplicated
per-route, since every route shares the identical
`_authorize_timeline_clip` gate and the interesting security behavior (who
gets rejected, when) is the same regardless of which specific mutation the
route performs.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db.session import get_db
from app.models.entities import Clip, MediaAsset, MediaStatus, MediaType, Project, Timeline, User


def _load_audio_enhancement_router():
    """Load app/api/v1/audio_enhancement.py directly, bypassing
    `app.api.__init__` — same reasoning as test_audio_description.py. This
    module imports app.tasks.audio_enhancement_tasks, which transitively
    needs boto3 (app.services.storage) and celery (app.worker), both
    installed for this CI slice — see backend-auth-tests.yml.
    """
    module_path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "audio_enhancement.py"
    spec = importlib.util.spec_from_file_location("_vantacut_test_audio_enhancement_router", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ae_module = _load_audio_enhancement_router()


class _RecordingResult:
    id = "fake-task-id"


class _RecordingTask:
    """Stands in for a real Celery task so tests can assert an unauthorized
    request never enqueues real audio processing work."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def delay(self, *args) -> _RecordingResult:
        self.calls.append(args)
        return _RecordingResult()


@pytest.fixture()
def enhance_audio_task(monkeypatch):
    fake = _RecordingTask()
    monkeypatch.setattr(_ae_module, "enhance_audio", fake)
    return fake


@pytest.fixture()
def enhance_studio_sound_task(monkeypatch):
    fake = _RecordingTask()
    monkeypatch.setattr(_ae_module, "enhance_studio_sound", fake)
    return fake


@pytest.fixture()
def app_client(db_session):
    app = FastAPI()
    app.include_router(_ae_module.router, prefix="/api/v1")

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
    project = Project(owner_id=owner.id, name="Batch 2A audio-enhancement test project")
    db_session.add(project)
    db_session.flush()
    return project


def _make_timeline(db_session, project: Project) -> Timeline:
    timeline = Timeline(project_id=project.id, name="Batch 2A test timeline")
    db_session.add(timeline)
    db_session.flush()
    return timeline


def _make_clip(db_session, timeline: Timeline, order_index: int = 0) -> Clip:
    asset = MediaAsset(
        project_id=timeline.project_id,
        filename="clip.mp4",
        storage_key=f"batch2a/{uuid.uuid4().hex}.mp4",
        media_type=MediaType.VIDEO,
        status=MediaStatus.READY,
    )
    db_session.add(asset)
    db_session.flush()
    clip = Clip(
        timeline_id=timeline.id,
        source_asset_id=asset.id,
        source_start=0,
        source_end=1,
        order_index=order_index,
    )
    db_session.add(clip)
    db_session.flush()
    return clip


# (method, path_suffix, json_body) for all 5 routes on this router.
ROUTE_CASES = [
    pytest.param("POST", "/noise-reduction", None, id="post-noise-reduction"),
    pytest.param("DELETE", "/noise-reduction", None, id="delete-noise-reduction"),
    pytest.param("POST", "/studio-sound", {"wet_mix": 50}, id="post-studio-sound"),
    pytest.param("PATCH", "/studio-sound", {"wet_mix": 50}, id="patch-studio-sound"),
    pytest.param("DELETE", "/studio-sound", None, id="delete-studio-sound"),
]


def _call(app_client, method: str, timeline_id, clip_id, suffix: str, body, headers=None):
    url = f"/api/v1/timelines/{timeline_id}/clips/{clip_id}{suffix}"
    return app_client.request(method, url, json=body, headers=headers or {})


@pytest.mark.parametrize("method, suffix, body", ROUTE_CASES)
def test_anonymous_rejected_and_no_processing_started(
    app_client, enhance_audio_task, enhance_studio_sound_task, db_session, method, suffix, body,
):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    clip = _make_clip(db_session, timeline)

    response = _call(app_client, method, timeline.id, clip.id, suffix, body)
    assert response.status_code == 401
    assert enhance_audio_task.calls == []
    assert enhance_studio_sound_task.calls == []


@pytest.mark.parametrize("method, suffix, body", ROUTE_CASES)
def test_invalid_token_rejected_and_no_processing_started(
    app_client, enhance_audio_task, enhance_studio_sound_task, db_session, method, suffix, body,
):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    clip = _make_clip(db_session, timeline)

    response = _call(app_client, method, timeline.id, clip.id, suffix, body, headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401
    assert enhance_audio_task.calls == []
    assert enhance_studio_sound_task.calls == []


@pytest.mark.parametrize("method, suffix, body", ROUTE_CASES)
def test_non_owner_rejected_cannot_mutate_or_start_processing(
    app_client, enhance_audio_task, enhance_studio_sound_task, db_session, method, suffix, body,
):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    clip = _make_clip(db_session, timeline)
    clip_id = clip.id
    effects_before = list(clip.audio_effects)
    stranger = _make_user(db_session)
    token = create_access_token(stranger.id)

    response = _call(app_client, method, timeline.id, clip.id, suffix, body, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404
    assert enhance_audio_task.calls == []
    assert enhance_studio_sound_task.calls == []

    # State was not mutated by the rejected request.
    db_session.expire_all()
    refreshed = db_session.get(Clip, clip_id)
    assert refreshed.audio_effects == effects_before


@pytest.mark.parametrize("method, suffix, body", ROUTE_CASES)
def test_cross_timeline_clip_rejected_idor(
    app_client, enhance_audio_task, enhance_studio_sound_task, db_session, method, suffix, body,
):
    """The clip legitimately exists and the timeline legitimately belongs to
    this caller — but the two don't belong to each other. Owning timeline_a
    must not be enough to reach a clip that actually lives on timeline_b,
    even though both are in the caller's own project."""
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline_a = _make_timeline(db_session, project)
    timeline_b = _make_timeline(db_session, project)
    clip_on_b = _make_clip(db_session, timeline_b)
    token = create_access_token(owner.id)

    response = _call(app_client, method, timeline_a.id, clip_on_b.id, suffix, body, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404
    assert enhance_audio_task.calls == []
    assert enhance_studio_sound_task.calls == []


def test_owner_noise_reduction_request_reaches_task_boundary(app_client, enhance_audio_task, enhance_studio_sound_task, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    clip = _make_clip(db_session, timeline)
    token = create_access_token(owner.id)

    response = app_client.post(
        f"/api/v1/timelines/{timeline.id}/clips/{clip.id}/noise-reduction",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    assert enhance_audio_task.calls == [(str(clip.id),)]
    assert enhance_studio_sound_task.calls == []


def test_owner_studio_sound_request_reaches_task_boundary(app_client, enhance_audio_task, enhance_studio_sound_task, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    clip = _make_clip(db_session, timeline)
    token = create_access_token(owner.id)

    response = app_client.post(
        f"/api/v1/timelines/{timeline.id}/clips/{clip.id}/studio-sound",
        json={"wet_mix": 60},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    assert enhance_studio_sound_task.calls == [(str(clip.id), 60)]
    assert enhance_audio_task.calls == []


def test_owner_disable_noise_reduction_mutates_state(app_client, enhance_audio_task, enhance_studio_sound_task, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    clip = _make_clip(db_session, timeline)
    clip.audio_effects = ["noise_reduction"]
    db_session.flush()
    token = create_access_token(owner.id)

    response = app_client.delete(
        f"/api/v1/timelines/{timeline.id}/clips/{clip.id}/noise-reduction",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204
    db_session.expire_all()
    refreshed = db_session.get(Clip, clip.id)
    assert "noise_reduction" not in refreshed.audio_effects


def test_disabled_owner_rejected_and_no_processing_started(app_client, enhance_audio_task, enhance_studio_sound_task, db_session):
    owner = _make_user(db_session)
    owner.is_active = False
    db_session.flush()
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    clip = _make_clip(db_session, timeline)
    token = create_access_token(owner.id)

    response = app_client.post(
        f"/api/v1/timelines/{timeline.id}/clips/{clip.id}/noise-reduction",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert enhance_audio_task.calls == []
