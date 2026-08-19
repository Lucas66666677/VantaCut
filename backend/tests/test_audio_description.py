"""Batch 2A security tests for POST /timelines/{timeline_id}/generate-audio-description
(app/api/v1/audio_description.py).

Mocks ONLY the Celery task boundary (`generate_audio_description.delay`).
Identity (get_current_user) and ownership (`_authorize_timeline_owner`) run
for real against the test database, exactly like Batch 1's
test_ai_analyze_video.py — never mocked.
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
from app.models.entities import MediaAsset, MediaStatus, MediaType, Project, Timeline, User


def _load_audio_description_router():
    """Load app/api/v1/audio_description.py directly, bypassing
    `app.api.__init__` — same reasoning as test_ai_analyze_video.py. This
    module additionally imports app.tasks.audio_description_tasks, which
    transitively imports app.services.storage (boto3) and app.worker
    (celery); both packages are installed for this CI slice specifically so
    this router can load — see backend-auth-tests.yml. Neither pulls in any
    ML/audio-processing dependency: app.services.storage's only third-party
    import is boto3/botocore, and celery_app's construction does not touch
    a broker at import time.
    """
    module_path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "audio_description.py"
    spec = importlib.util.spec_from_file_location("_vantacut_test_audio_description_router", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ad_module = _load_audio_description_router()


class _RecordingResult:
    id = "fake-task-id"


class _RecordingTask:
    """Stands in for the real Celery task so tests can assert an
    unauthorized request never enqueues background generation work."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def delay(self, timeline_id: str) -> _RecordingResult:
        self.calls.append(timeline_id)
        return _RecordingResult()


@pytest.fixture()
def task(monkeypatch):
    fake = _RecordingTask()
    monkeypatch.setattr(_ad_module, "generate_audio_description", fake)
    return fake


@pytest.fixture()
def app_client(db_session):
    app = FastAPI()
    app.include_router(_ad_module.router, prefix="/api/v1")

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
    project = Project(owner_id=owner.id, name="Batch 2A audio-description test project")
    db_session.add(project)
    db_session.flush()
    return project


def _make_confirmed_timeline(db_session, project: Project) -> tuple[Timeline, MediaAsset]:
    asset = MediaAsset(
        project_id=project.id,
        filename="clip.mp4",
        storage_key=f"batch2a/{uuid.uuid4().hex}.mp4",
        media_type=MediaType.VIDEO,
        status=MediaStatus.READY,
    )
    db_session.add(asset)
    db_session.flush()
    timeline = Timeline(
        project_id=project.id,
        name="Batch 2A test timeline",
        settings_json={"confirmed_timeline": {"source_asset_id": str(asset.id)}},
    )
    db_session.add(timeline)
    db_session.flush()
    return timeline, asset


def _request_body(asset: MediaAsset) -> dict:
    return {"source_asset_id": str(asset.id), "language": "zh", "min_gap_seconds": 2.0, "mode": "standard"}


def test_anonymous_rejected_and_task_never_enqueued(app_client, task, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline, asset = _make_confirmed_timeline(db_session, project)

    response = app_client.post(
        f"/api/v1/timelines/{timeline.id}/generate-audio-description",
        json=_request_body(asset),
    )
    assert response.status_code == 401
    assert task.calls == []


def test_invalid_token_rejected_and_task_never_enqueued(app_client, task, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline, asset = _make_confirmed_timeline(db_session, project)

    response = app_client.post(
        f"/api/v1/timelines/{timeline.id}/generate-audio-description",
        json=_request_body(asset),
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401
    assert task.calls == []


def test_non_owner_rejected_and_task_never_enqueued(app_client, task, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline, asset = _make_confirmed_timeline(db_session, project)
    stranger = _make_user(db_session)
    token = create_access_token(stranger.id)

    response = app_client.post(
        f"/api/v1/timelines/{timeline.id}/generate-audio-description",
        json=_request_body(asset),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert task.calls == []


def test_unknown_timeline_rejected_non_enumerating(app_client, task, db_session):
    owner = _make_user(db_session)
    token = create_access_token(owner.id)

    response = app_client.post(
        f"/api/v1/timelines/{uuid.uuid4()}/generate-audio-description",
        json={"source_asset_id": str(uuid.uuid4())},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert task.calls == []


def test_disabled_owner_rejected_and_task_never_enqueued(app_client, task, db_session):
    owner = _make_user(db_session)
    owner.is_active = False
    db_session.flush()
    project = _make_project(db_session, owner)
    timeline, asset = _make_confirmed_timeline(db_session, project)
    token = create_access_token(owner.id)

    response = app_client.post(
        f"/api/v1/timelines/{timeline.id}/generate-audio-description",
        json=_request_body(asset),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert task.calls == []


def test_owner_request_reaches_task_boundary(app_client, task, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline, asset = _make_confirmed_timeline(db_session, project)
    token = create_access_token(owner.id)

    response = app_client.post(
        f"/api/v1/timelines/{timeline.id}/generate-audio-description",
        json=_request_body(asset),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    assert task.calls == [str(timeline.id)]
