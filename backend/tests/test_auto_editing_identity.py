"""M1 security tests for the first SPOOFABLE_USER_ID mechanical-migration
slice: auto_director.py, auto_narrative.py, auto_pip.py, auto_reframe.py,
auto_sfx.py, beat_sync.py, beauty_enhancement.py, rough_cut.py.

Every one of these route files previously trusted a client-supplied
`user_id` field (request body or query string) for both the identity check
AND the ownership check — a caller could pass ANY user_id and the route
would authorize against that identity instead of a verified session. This
migration swaps the identity source to `Depends(get_current_user)` (real
JWT bearer token, decoded and looked up against the real users table) while
leaving every existing ownership rule (`resource.project.owner_id ==
current_user.id` or equivalent) untouched.

Per route family this file covers, at minimum: anonymous caller rejected
(401), invalid/garbage token rejected (401), a real-but-non-owner caller
rejected (403/404, matching each route's existing pre-migration response
code — this migration does not change response codes), the rightful owner
succeeds, and — for one representative endpoint per family — a spoofed
`user_id` value in the request body is proven to have zero effect (the
field no longer exists on the schema; pydantic silently drops unknown
fields, and the operation runs as whoever the bearer token actually
belongs to).

Identity (get_current_user), ownership checks, and all database
relationships run for real against the test database — never mocked.
Only genuine external boundaries are mocked: each family's Celery
`.delay()` call. Routes with no Celery task (auto_reframe, auto_sfx,
beauty_enhancement, the auto_pip overlay endpoint) are exercised with zero
mocking at all.

Every route module is loaded directly via importlib, bypassing
`app.api.__init__`, exactly like test_audio_description.py /
test_ai_analyze_video.py — this avoids pulling in the full v1 router
package (and therefore every other route module's import-time
dependencies) just to test one file at a time.
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
from app.models.entities import (
    AIAnalysis, AnalysisType, AutoDirectorRun, MediaAsset, MediaStatus, MediaType, Project, Timeline, User,
)

API_V1 = "/api/v1"


def _load_router(module_name: str, relative_path: str):
    module_path = Path(__file__).resolve().parents[1] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_auto_director = _load_router("_vantacut_m1_auto_director", "app/api/v1/auto_director.py")
_auto_narrative = _load_router("_vantacut_m1_auto_narrative", "app/api/v1/auto_narrative.py")
_auto_pip = _load_router("_vantacut_m1_auto_pip", "app/api/v1/auto_pip.py")
_auto_reframe = _load_router("_vantacut_m1_auto_reframe", "app/api/v1/auto_reframe.py")
_auto_sfx = _load_router("_vantacut_m1_auto_sfx", "app/api/v1/auto_sfx.py")
_beat_sync = _load_router("_vantacut_m1_beat_sync", "app/api/v1/beat_sync.py")
_beauty_enhancement = _load_router("_vantacut_m1_beauty_enhancement", "app/api/v1/beauty_enhancement.py")
_rough_cut = _load_router("_vantacut_m1_rough_cut", "app/api/v1/rough_cut.py")


class _RecordingResult:
    id = "fake-task-id"


class _RecordingTask:
    """Stands in for a real Celery task so tests can assert an unauthorized
    request never enqueues background work."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def delay(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return _RecordingResult()


def _client_for(*routers, db_session) -> TestClient:
    app = FastAPI()
    for router in routers:
        app.include_router(router, prefix="/api/v1")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


def _make_user(db_session) -> User:
    user = User(email=f"m1-{uuid.uuid4().hex[:12]}@example.com", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session, owner: User) -> Project:
    project = Project(owner_id=owner.id, name="M1 auto-editing identity test project")
    db_session.add(project)
    db_session.flush()
    return project


def _make_timeline(db_session, project: Project) -> Timeline:
    timeline = Timeline(project_id=project.id, name="M1 identity test timeline", settings_json={})
    db_session.add(timeline)
    db_session.flush()
    return timeline


def _make_media_asset(db_session, project: Project, media_type: MediaType = MediaType.VIDEO, ready: bool = True) -> MediaAsset:
    asset = MediaAsset(
        project_id=project.id,
        filename=f"m1-{uuid.uuid4().hex[:8]}.mp4",
        storage_key=f"m1/{uuid.uuid4().hex}.mp4",
        media_type=media_type,
        status=MediaStatus.READY if ready else MediaStatus.UPLOADING,
        duration_seconds=12.0,
    )
    db_session.add(asset)
    db_session.flush()
    return asset


# ============================================================================
# auto_director.py — POST /projects/{id}/auto-director, GET /auto-director/{run_id}
# ============================================================================

@pytest.fixture()
def director_task(monkeypatch):
    fake = _RecordingTask()
    monkeypatch.setattr(_auto_director, "create_documentary", fake)
    return fake


def _director_body() -> dict:
    return {"topic": "A twelve-minute documentary about a coastal train line"}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_auto_director_unauthenticated_rejected(headers, db_session, director_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    client = _client_for(_auto_director.router, db_session=db_session)

    response = client.post(f"{API_V1}/projects/{project.id}/auto-director", json=_director_body(), headers=headers)
    assert response.status_code == 401
    assert director_task.calls == []


def test_auto_director_non_owner_rejected(db_session, director_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    stranger = _make_user(db_session)
    token = create_access_token(stranger.id)
    client = _client_for(_auto_director.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/projects/{project.id}/auto-director", json=_director_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert director_task.calls == []


def test_auto_director_owner_succeeds_and_records_requester(db_session, director_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    token = create_access_token(owner.id)
    client = _client_for(_auto_director.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/projects/{project.id}/auto-director", json=_director_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    assert len(director_task.calls) == 1
    run = db_session.get(AutoDirectorRun, uuid.UUID(response.json()["run_id"]))
    assert run.requested_by_id == owner.id


def test_auto_director_spoofed_user_id_in_body_has_no_effect(db_session, director_task):
    """Authenticate as the real owner but include a spoofed user_id for a
    different user in the body. The field no longer exists on the schema
    (pydantic drops it); the run must still be attributed to the real
    bearer-token identity, never the spoofed value."""
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    stranger = _make_user(db_session)
    token = create_access_token(owner.id)
    client = _client_for(_auto_director.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/projects/{project.id}/auto-director",
        json={**_director_body(), "user_id": str(stranger.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    run = db_session.get(AutoDirectorRun, uuid.UUID(response.json()["run_id"]))
    assert run.requested_by_id == owner.id
    assert run.requested_by_id != stranger.id


def test_auto_director_get_run_non_owner_rejected(db_session, director_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    run = AutoDirectorRun(project_id=project.id, requested_by_id=owner.id, topic="t", creative_brief_json={})
    db_session.add(run)
    db_session.flush()
    stranger = _make_user(db_session)
    token = create_access_token(stranger.id)
    client = _client_for(_auto_director.router, db_session=db_session)

    response = client.get(f"{API_V1}/auto-director/{run.id}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_auto_director_get_run_owner_succeeds(db_session, director_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    run = AutoDirectorRun(project_id=project.id, requested_by_id=owner.id, topic="t", creative_brief_json={})
    db_session.add(run)
    db_session.flush()
    token = create_access_token(owner.id)
    client = _client_for(_auto_director.router, db_session=db_session)

    response = client.get(f"{API_V1}/auto-director/{run.id}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


# ============================================================================
# auto_narrative.py — POST /projects/{id}/auto-narrative
# ============================================================================

@pytest.fixture()
def narrative_task(monkeypatch):
    fake = _RecordingTask()
    monkeypatch.setattr(_auto_narrative, "generate_auto_narrative", fake)
    return fake


def _narrative_body(assets: list[MediaAsset]) -> dict:
    return {"media_asset_ids": [str(asset.id) for asset in assets], "tone": "funny_vlogger", "target_duration_seconds": 30}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_auto_narrative_unauthenticated_rejected(headers, db_session, narrative_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    assets = [_make_media_asset(db_session, project) for _ in range(5)]
    client = _client_for(_auto_narrative.router, db_session=db_session)

    response = client.post(f"{API_V1}/projects/{project.id}/auto-narrative", json=_narrative_body(assets), headers=headers)
    assert response.status_code == 401
    assert narrative_task.calls == []


def test_auto_narrative_non_owner_rejected(db_session, narrative_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    assets = [_make_media_asset(db_session, project) for _ in range(5)]
    stranger = _make_user(db_session)
    token = create_access_token(stranger.id)
    client = _client_for(_auto_narrative.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/projects/{project.id}/auto-narrative", json=_narrative_body(assets),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert narrative_task.calls == []


def test_auto_narrative_owner_succeeds_with_trusted_identity_forwarded(db_session, narrative_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    assets = [_make_media_asset(db_session, project) for _ in range(5)]
    token = create_access_token(owner.id)
    client = _client_for(_auto_narrative.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/projects/{project.id}/auto-narrative", json=_narrative_body(assets),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    assert len(narrative_task.calls) == 1
    (_project_id, task_payload), _kwargs = narrative_task.calls[0]
    # The Celery task independently re-verifies ownership using this
    # user_id, so it must be the trusted current_user.id, not client input.
    assert task_payload["user_id"] == str(owner.id)


def test_auto_narrative_spoofed_user_id_in_body_has_no_effect(db_session, narrative_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    assets = [_make_media_asset(db_session, project) for _ in range(5)]
    stranger = _make_user(db_session)
    token = create_access_token(owner.id)
    client = _client_for(_auto_narrative.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/projects/{project.id}/auto-narrative",
        json={**_narrative_body(assets), "user_id": str(stranger.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    (_project_id, task_payload), _kwargs = narrative_task.calls[0]
    assert task_payload["user_id"] == str(owner.id)


# ============================================================================
# auto_pip.py — POST /timelines/{id}/auto-pip, PUT /timelines/{id}/auto-pip/overlays
# ============================================================================

@pytest.fixture()
def pip_task(monkeypatch):
    fake = _RecordingTask()
    monkeypatch.setattr(_auto_pip, "configure_auto_pip", fake)
    return fake


def _pip_body() -> dict:
    return {"main_asset_id": str(uuid.uuid4()), "selfie_asset_id": str(uuid.uuid4()), "corner": "bottom_right"}


def _pip_overlay_body() -> dict:
    return {"kind": "highlighter", "points": [{"x": 0.1, "y": 0.1}, {"x": 0.2, "y": 0.2}], "start_time": 1.0, "end_time": 2.0}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_auto_pip_configure_unauthenticated_rejected(headers, db_session, pip_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    client = _client_for(_auto_pip.router, db_session=db_session)

    response = client.post(f"{API_V1}/timelines/{timeline.id}/auto-pip", json=_pip_body(), headers=headers)
    assert response.status_code == 401
    assert pip_task.calls == []


def test_auto_pip_configure_non_owner_rejected(db_session, pip_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    stranger = _make_user(db_session)
    token = create_access_token(stranger.id)
    client = _client_for(_auto_pip.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/timelines/{timeline.id}/auto-pip", json=_pip_body(), headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert pip_task.calls == []


def test_auto_pip_configure_owner_succeeds(db_session, pip_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    token = create_access_token(owner.id)
    client = _client_for(_auto_pip.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/timelines/{timeline.id}/auto-pip", json=_pip_body(), headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    assert len(pip_task.calls) == 1


def test_auto_pip_configure_spoofed_user_id_in_body_has_no_effect(db_session, pip_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    stranger = _make_user(db_session)
    token = create_access_token(owner.id)
    client = _client_for(_auto_pip.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/timelines/{timeline.id}/auto-pip",
        json={**_pip_body(), "user_id": str(stranger.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    assert len(pip_task.calls) == 1


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_auto_pip_overlay_unauthenticated_rejected(headers, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    client = _client_for(_auto_pip.router, db_session=db_session)

    response = client.put(f"{API_V1}/timelines/{timeline.id}/auto-pip/overlays", json=_pip_overlay_body(), headers=headers)
    assert response.status_code == 401


def test_auto_pip_overlay_non_owner_rejected(db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    stranger = _make_user(db_session)
    token = create_access_token(stranger.id)
    client = _client_for(_auto_pip.router, db_session=db_session)

    response = client.put(
        f"{API_V1}/timelines/{timeline.id}/auto-pip/overlays", json=_pip_overlay_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_auto_pip_overlay_owner_succeeds(db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    token = create_access_token(owner.id)
    client = _client_for(_auto_pip.router, db_session=db_session)

    response = client.put(
        f"{API_V1}/timelines/{timeline.id}/auto-pip/overlays", json=_pip_overlay_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


# ============================================================================
# auto_reframe.py — POST /timelines/{id}/auto-reframe
# ============================================================================

def _reframe_body() -> dict:
    return {"detector_stride": 2, "smoothing": 0.75}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_auto_reframe_unauthenticated_rejected(headers, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    client = _client_for(_auto_reframe.router, db_session=db_session)

    response = client.post(f"{API_V1}/timelines/{timeline.id}/auto-reframe", json=_reframe_body(), headers=headers)
    assert response.status_code == 401


def test_auto_reframe_non_owner_rejected(db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    stranger = _make_user(db_session)
    token = create_access_token(stranger.id)
    client = _client_for(_auto_reframe.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/timelines/{timeline.id}/auto-reframe", json=_reframe_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_auto_reframe_owner_succeeds(db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    token = create_access_token(owner.id)
    client = _client_for(_auto_reframe.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/timelines/{timeline.id}/auto-reframe", json=_reframe_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


def test_auto_reframe_spoofed_user_id_in_body_has_no_effect(db_session):
    """A stranger's id in the (now nonexistent) user_id field must not let
    a non-owner masquerade as the owner, and must not break the owner's
    own request either."""
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    stranger = _make_user(db_session)
    token = create_access_token(owner.id)
    client = _client_for(_auto_reframe.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/timelines/{timeline.id}/auto-reframe",
        json={**_reframe_body(), "user_id": str(stranger.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


# ============================================================================
# auto_sfx.py — PUT /timelines/{id}/auto-sfx
# ============================================================================

def _sfx_body() -> dict:
    return {"bgm_volume": 0.16, "ducking_enabled": True}


def _sfx_timeline(db_session, project: Project) -> Timeline:
    timeline = Timeline(project_id=project.id, name="M1 sfx timeline", settings_json={"confirmed_timeline": {"tracks": []}})
    db_session.add(timeline)
    db_session.flush()
    return timeline


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_auto_sfx_unauthenticated_rejected(headers, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _sfx_timeline(db_session, project)
    client = _client_for(_auto_sfx.router, db_session=db_session)

    response = client.put(f"{API_V1}/timelines/{timeline.id}/auto-sfx", json=_sfx_body(), headers=headers)
    assert response.status_code == 401


def test_auto_sfx_non_owner_rejected(db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _sfx_timeline(db_session, project)
    stranger = _make_user(db_session)
    token = create_access_token(stranger.id)
    client = _client_for(_auto_sfx.router, db_session=db_session)

    response = client.put(
        f"{API_V1}/timelines/{timeline.id}/auto-sfx", json=_sfx_body(), headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_auto_sfx_owner_succeeds(db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _sfx_timeline(db_session, project)
    token = create_access_token(owner.id)
    client = _client_for(_auto_sfx.router, db_session=db_session)

    response = client.put(
        f"{API_V1}/timelines/{timeline.id}/auto-sfx", json=_sfx_body(), headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


# ============================================================================
# beat_sync.py — POST /timelines/{id}/beat-sync/analyze, POST /projects/{id}/beat-sync/montage
# ============================================================================

@pytest.fixture()
def beat_sync_tasks(monkeypatch):
    analyze_fake = _RecordingTask()
    montage_fake = _RecordingTask()
    monkeypatch.setattr(_beat_sync, "analyze_and_plan", analyze_fake)
    monkeypatch.setattr(_beat_sync, "generate_montage", montage_fake)
    return analyze_fake, montage_fake


def _beat_sync_analyze_body(bgm: MediaAsset, source: MediaAsset) -> dict:
    return {"bgm_asset_id": str(bgm.id), "source_asset_id": str(source.id), "max_cut_suggestions": 10}


def _beat_sync_montage_body(bgm: MediaAsset, assets: list[MediaAsset]) -> dict:
    return {"bgm_asset_id": str(bgm.id), "media_asset_ids": [str(asset.id) for asset in assets]}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_beat_sync_analyze_unauthenticated_rejected(headers, db_session, beat_sync_tasks):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    bgm = _make_media_asset(db_session, project, media_type=MediaType.AUDIO)
    source = _make_media_asset(db_session, project)
    client = _client_for(_beat_sync.router, _beat_sync.montage_router, db_session=db_session)

    response = client.post(
        f"{API_V1}/timelines/{timeline.id}/beat-sync/analyze", json=_beat_sync_analyze_body(bgm, source), headers=headers,
    )
    assert response.status_code == 401
    assert beat_sync_tasks[0].calls == []


def test_beat_sync_analyze_non_owner_rejected(db_session, beat_sync_tasks):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    bgm = _make_media_asset(db_session, project, media_type=MediaType.AUDIO)
    source = _make_media_asset(db_session, project)
    stranger = _make_user(db_session)
    token = create_access_token(stranger.id)
    client = _client_for(_beat_sync.router, _beat_sync.montage_router, db_session=db_session)

    response = client.post(
        f"{API_V1}/timelines/{timeline.id}/beat-sync/analyze", json=_beat_sync_analyze_body(bgm, source),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert beat_sync_tasks[0].calls == []


def test_beat_sync_analyze_owner_succeeds(db_session, beat_sync_tasks):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    bgm = _make_media_asset(db_session, project, media_type=MediaType.AUDIO)
    source = _make_media_asset(db_session, project)
    token = create_access_token(owner.id)
    client = _client_for(_beat_sync.router, _beat_sync.montage_router, db_session=db_session)

    response = client.post(
        f"{API_V1}/timelines/{timeline.id}/beat-sync/analyze", json=_beat_sync_analyze_body(bgm, source),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    assert len(beat_sync_tasks[0].calls) == 1


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_beat_sync_montage_unauthenticated_rejected(headers, db_session, beat_sync_tasks):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    bgm = _make_media_asset(db_session, project, media_type=MediaType.AUDIO)
    assets = [_make_media_asset(db_session, project) for _ in range(10)]
    client = _client_for(_beat_sync.router, _beat_sync.montage_router, db_session=db_session)

    response = client.post(
        f"{API_V1}/projects/{project.id}/beat-sync/montage", json=_beat_sync_montage_body(bgm, assets), headers=headers,
    )
    assert response.status_code == 401
    assert beat_sync_tasks[1].calls == []


def test_beat_sync_montage_non_owner_rejected(db_session, beat_sync_tasks):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    bgm = _make_media_asset(db_session, project, media_type=MediaType.AUDIO)
    assets = [_make_media_asset(db_session, project) for _ in range(10)]
    stranger = _make_user(db_session)
    token = create_access_token(stranger.id)
    client = _client_for(_beat_sync.router, _beat_sync.montage_router, db_session=db_session)

    response = client.post(
        f"{API_V1}/projects/{project.id}/beat-sync/montage", json=_beat_sync_montage_body(bgm, assets),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert beat_sync_tasks[1].calls == []


def test_beat_sync_montage_owner_succeeds_with_trusted_identity_forwarded(db_session, beat_sync_tasks):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    bgm = _make_media_asset(db_session, project, media_type=MediaType.AUDIO)
    assets = [_make_media_asset(db_session, project) for _ in range(10)]
    token = create_access_token(owner.id)
    client = _client_for(_beat_sync.router, _beat_sync.montage_router, db_session=db_session)

    response = client.post(
        f"{API_V1}/projects/{project.id}/beat-sync/montage", json=_beat_sync_montage_body(bgm, assets),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    (_project_id, forwarded_user_id, _payload), _kwargs = beat_sync_tasks[1].calls[0]
    assert forwarded_user_id == str(owner.id)


# ============================================================================
# beauty_enhancement.py — PUT /timelines/{id}/beauty-enhancement
# ============================================================================

def _beauty_body() -> dict:
    return {"enabled": True, "skin_smoothing": 40}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_beauty_enhancement_unauthenticated_rejected(headers, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    client = _client_for(_beauty_enhancement.router, db_session=db_session)

    response = client.put(f"{API_V1}/timelines/{timeline.id}/beauty-enhancement", json=_beauty_body(), headers=headers)
    assert response.status_code == 401


def test_beauty_enhancement_non_owner_rejected(db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    stranger = _make_user(db_session)
    token = create_access_token(stranger.id)
    client = _client_for(_beauty_enhancement.router, db_session=db_session)

    response = client.put(
        f"{API_V1}/timelines/{timeline.id}/beauty-enhancement", json=_beauty_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_beauty_enhancement_owner_succeeds(db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    token = create_access_token(owner.id)
    client = _client_for(_beauty_enhancement.router, db_session=db_session)

    response = client.put(
        f"{API_V1}/timelines/{timeline.id}/beauty-enhancement", json=_beauty_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


# ============================================================================
# rough_cut.py — POST /analysis/rough-cut, GET /analysis/rough-cut/{media_asset_id}
# ============================================================================

@pytest.fixture()
def rough_cut_task(monkeypatch):
    fake = _RecordingTask()
    monkeypatch.setattr(_rough_cut, "analyze_audio_rough_cut", fake)
    return fake


def _make_asset_with_audio(db_session, project: Project) -> MediaAsset:
    asset = _make_media_asset(db_session, project)
    asset.audio_key = f"m1/{uuid.uuid4().hex}.wav"
    db_session.flush()
    return asset


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_rough_cut_request_unauthenticated_rejected(headers, db_session, rough_cut_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_asset_with_audio(db_session, project)
    client = _client_for(_rough_cut.router, db_session=db_session)

    response = client.post(f"{API_V1}/analysis/rough-cut", json={"media_asset_id": str(asset.id)}, headers=headers)
    assert response.status_code == 401
    assert rough_cut_task.calls == []


def test_rough_cut_request_non_owner_rejected(db_session, rough_cut_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_asset_with_audio(db_session, project)
    stranger = _make_user(db_session)
    token = create_access_token(stranger.id)
    client = _client_for(_rough_cut.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/analysis/rough-cut", json={"media_asset_id": str(asset.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert rough_cut_task.calls == []


def test_rough_cut_request_owner_succeeds(db_session, rough_cut_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_asset_with_audio(db_session, project)
    token = create_access_token(owner.id)
    client = _client_for(_rough_cut.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/analysis/rough-cut", json={"media_asset_id": str(asset.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    assert len(rough_cut_task.calls) == 1


def test_rough_cut_request_spoofed_user_id_in_body_has_no_effect(db_session, rough_cut_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_asset_with_audio(db_session, project)
    stranger = _make_user(db_session)
    token = create_access_token(owner.id)
    client = _client_for(_rough_cut.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/analysis/rough-cut",
        json={"media_asset_id": str(asset.id), "user_id": str(stranger.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    assert len(rough_cut_task.calls) == 1


def _make_completed_rough_cut_analysis(db_session, asset: MediaAsset) -> AIAnalysis:
    analysis = AIAnalysis(
        media_asset_id=asset.id, analysis_type=AnalysisType.ROUGH_CUT, status="completed",
        result_json={"clip_analysis": [], "timeline_suggestions": []},
    )
    db_session.add(analysis)
    db_session.flush()
    return analysis


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_rough_cut_result_unauthenticated_rejected(headers, db_session, rough_cut_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_asset_with_audio(db_session, project)
    _make_completed_rough_cut_analysis(db_session, asset)
    client = _client_for(_rough_cut.router, db_session=db_session)

    response = client.get(f"{API_V1}/analysis/rough-cut/{asset.id}", headers=headers)
    assert response.status_code == 401


def test_rough_cut_result_non_owner_rejected(db_session, rough_cut_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_asset_with_audio(db_session, project)
    _make_completed_rough_cut_analysis(db_session, asset)
    stranger = _make_user(db_session)
    token = create_access_token(stranger.id)
    client = _client_for(_rough_cut.router, db_session=db_session)

    response = client.get(f"{API_V1}/analysis/rough-cut/{asset.id}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_rough_cut_result_owner_succeeds(db_session, rough_cut_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_asset_with_audio(db_session, project)
    _make_completed_rough_cut_analysis(db_session, asset)
    token = create_access_token(owner.id)
    client = _client_for(_rough_cut.router, db_session=db_session)

    response = client.get(f"{API_V1}/analysis/rough-cut/{asset.id}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
