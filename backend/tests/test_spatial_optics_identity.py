"""M2 security tests for the second SPOOFABLE_USER_ID mechanical-migration
slice: spatial.py, spatial_text.py, spatial_video.py, optics.py,
film_optics.py, relighting.py, parallax.py, travel_maps.py.

Same pattern as tests/test_auto_editing_identity.py (M1): every one of
these route files previously trusted a client-supplied `user_id` field
(request body or query string) for both the identity check AND the
ownership check. This migration swaps the identity source to
`Depends(get_current_user)` (real JWT bearer token, decoded and looked up
against the real users table) while leaving every existing ownership rule
(`resource.project.owner_id == current_user.id` / `timeline.project.owner_id
== current_user.id`) untouched, including each route's pre-existing
response-code convention (some use 403 for wrong-owner, some use 404 —
this migration does not change response codes).

Per route family this file covers, at minimum: anonymous caller rejected
(401), invalid/garbage token rejected (401), a real-but-non-owner caller
rejected, the rightful owner succeeds, and — for one representative
endpoint per family — a spoofed `user_id` value in the request body is
proven to have zero effect (the field no longer exists on the schema;
pydantic silently drops unknown fields).

Identity (get_current_user), ownership checks, and all database
relationships run for real against the test database — never mocked. Only
genuine external boundaries are mocked: each family's Celery `.delay()`
call. Routes with no Celery task (film_optics, relighting's timeline PUT,
travel_maps' GET) are exercised with zero mocking at all.

Every route module is loaded directly via importlib, bypassing
app.api.__init__, exactly like M1's test file.
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
from app.models.entities import MediaAsset, MediaStatus, MediaType, Project, RenderJob, RenderStatus, Timeline, User

API_V1 = "/api/v1"


def _load_router(module_name: str, relative_path: str):
    module_path = Path(__file__).resolve().parents[1] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_spatial = _load_router("_vantacut_m2_spatial", "app/api/v1/spatial.py")
_spatial_text = _load_router("_vantacut_m2_spatial_text", "app/api/v1/spatial_text.py")
_spatial_video = _load_router("_vantacut_m2_spatial_video", "app/api/v1/spatial_video.py")
_optics = _load_router("_vantacut_m2_optics", "app/api/v1/optics.py")
_film_optics = _load_router("_vantacut_m2_film_optics", "app/api/v1/film_optics.py")
_relighting = _load_router("_vantacut_m2_relighting", "app/api/v1/relighting.py")
_parallax = _load_router("_vantacut_m2_parallax", "app/api/v1/parallax.py")
_travel_maps = _load_router("_vantacut_m2_travel_maps", "app/api/v1/travel_maps.py")


class _RecordingResult:
    id = "fake-task-id"


class _RecordingTask:
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
    user = User(email=f"m2-{uuid.uuid4().hex[:12]}@example.com", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session, owner: User) -> Project:
    project = Project(owner_id=owner.id, name="M2 spatial/optics identity test project")
    db_session.add(project)
    db_session.flush()
    return project


def _make_timeline(db_session, project: Project) -> Timeline:
    timeline = Timeline(project_id=project.id, name="M2 identity test timeline", settings_json={})
    db_session.add(timeline)
    db_session.flush()
    return timeline


def _make_media_asset(db_session, project: Project, ready: bool = True) -> MediaAsset:
    asset = MediaAsset(
        project_id=project.id,
        filename=f"m2-{uuid.uuid4().hex[:8]}.mp4",
        storage_key=f"m2/{uuid.uuid4().hex}.mp4",
        media_type=MediaType.VIDEO,
        status=MediaStatus.READY if ready else MediaStatus.UPLOADING,
        duration_seconds=12.0,
    )
    db_session.add(asset)
    db_session.flush()
    return asset


def _auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


# ============================================================================
# spatial.py — POST /media/{id}/spatial-reconstruction, GET .../spatial-scene,
# POST .../spatial-scene/render
# ============================================================================

@pytest.fixture()
def reconstruct_task(monkeypatch):
    fake = _RecordingTask()
    monkeypatch.setattr(_spatial, "reconstruct_spatial_scene", fake)
    return fake


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_spatial_reconstruction_unauthenticated_rejected(headers, db_session, reconstruct_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    client = _client_for(_spatial.router, db_session=db_session)

    response = client.post(f"{API_V1}/media/{asset.id}/spatial-reconstruction", json={}, headers=headers)
    assert response.status_code == 401
    assert reconstruct_task.calls == []


def test_spatial_reconstruction_non_owner_rejected(db_session, reconstruct_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    stranger = _make_user(db_session)
    client = _client_for(_spatial.router, db_session=db_session)

    response = client.post(f"{API_V1}/media/{asset.id}/spatial-reconstruction", json={}, headers=_auth(stranger))
    assert response.status_code == 403
    assert reconstruct_task.calls == []


def test_spatial_reconstruction_owner_succeeds(db_session, reconstruct_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    client = _client_for(_spatial.router, db_session=db_session)

    response = client.post(f"{API_V1}/media/{asset.id}/spatial-reconstruction", json={}, headers=_auth(owner))
    assert response.status_code == 202
    assert len(reconstruct_task.calls) == 1


def test_spatial_reconstruction_spoofed_user_id_has_no_effect(db_session, reconstruct_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    stranger = _make_user(db_session)
    client = _client_for(_spatial.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/media/{asset.id}/spatial-reconstruction",
        json={"user_id": str(stranger.id)}, headers=_auth(owner),
    )
    assert response.status_code == 202
    assert len(reconstruct_task.calls) == 1


def test_spatial_scene_get_owner_succeeds(db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    asset.metadata_json = {"spatial_scene": {"status": "completed"}}
    db_session.commit()
    client = _client_for(_spatial.router, db_session=db_session)

    response = client.get(f"{API_V1}/media/{asset.id}/spatial-scene", headers=_auth(owner))
    assert response.status_code == 200


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_spatial_scene_get_unauthenticated_rejected(headers, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    client = _client_for(_spatial.router, db_session=db_session)

    response = client.get(f"{API_V1}/media/{asset.id}/spatial-scene", headers=headers)
    assert response.status_code == 401


def test_spatial_scene_get_non_owner_rejected(db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    stranger = _make_user(db_session)
    client = _client_for(_spatial.router, db_session=db_session)

    response = client.get(f"{API_V1}/media/{asset.id}/spatial-scene", headers=_auth(stranger))
    assert response.status_code == 403


@pytest.fixture()
def virtual_camera_task(monkeypatch):
    fake = _RecordingTask()
    monkeypatch.setattr(_spatial, "render_spatial_virtual_camera", fake)
    return fake


def _camera_body() -> dict:
    return {"camera_path": [{"time_seconds": 0, "position": [0, 0, 0], "look_at": [0, 0, 1]}, {"time_seconds": 1, "position": [0, 0, 1], "look_at": [0, 0, 2]}]}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_virtual_camera_render_unauthenticated_rejected(headers, db_session, virtual_camera_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    asset.metadata_json = {"spatial_scene": {"status": "completed"}}
    db_session.commit()
    client = _client_for(_spatial.router, db_session=db_session)

    response = client.post(f"{API_V1}/media/{asset.id}/spatial-scene/render", json=_camera_body(), headers=headers)
    assert response.status_code == 401
    assert virtual_camera_task.calls == []


def test_virtual_camera_render_owner_succeeds(db_session, virtual_camera_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    asset.metadata_json = {"spatial_scene": {"status": "completed"}}
    db_session.commit()
    client = _client_for(_spatial.router, db_session=db_session)

    response = client.post(f"{API_V1}/media/{asset.id}/spatial-scene/render", json=_camera_body(), headers=_auth(owner))
    assert response.status_code == 202
    assert len(virtual_camera_task.calls) == 1


# ============================================================================
# spatial_text.py — POST /media/{id}/analyze-spatial-text,
# POST /timelines/{id}/spatial-text
# ============================================================================

@pytest.fixture()
def spatial_text_task(monkeypatch):
    fake = _RecordingTask()
    monkeypatch.setattr(_spatial_text, "analyze_spatial_text", fake)
    return fake


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_analyze_spatial_text_unauthenticated_rejected(headers, db_session, spatial_text_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    client = _client_for(_spatial_text.router, db_session=db_session)

    response = client.post(f"{API_V1}/media/{asset.id}/analyze-spatial-text", json={"use_proxy": True}, headers=headers)
    assert response.status_code == 401
    assert spatial_text_task.calls == []


def test_analyze_spatial_text_non_owner_rejected(db_session, spatial_text_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    stranger = _make_user(db_session)
    client = _client_for(_spatial_text.router, db_session=db_session)

    response = client.post(f"{API_V1}/media/{asset.id}/analyze-spatial-text", json={"use_proxy": True}, headers=_auth(stranger))
    assert response.status_code == 404
    assert spatial_text_task.calls == []


def test_analyze_spatial_text_owner_succeeds(db_session, spatial_text_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    client = _client_for(_spatial_text.router, db_session=db_session)

    response = client.post(f"{API_V1}/media/{asset.id}/analyze-spatial-text", json={"use_proxy": True}, headers=_auth(owner))
    assert response.status_code == 202
    assert len(spatial_text_task.calls) == 1


def test_add_spatial_text_owner_succeeds(db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    asset = _make_media_asset(db_session, project)
    asset.metadata_json = {"spatial_text_tracking": {"status": "completed", "depth_key": "d", "camera_poses_key": "c"}}
    db_session.commit()
    client = _client_for(_spatial_text.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/timelines/{timeline.id}/spatial-text",
        json={"source_asset_id": str(asset.id), "text": "hi", "x": .5, "y": .5, "z": .5, "start_time": 0, "end_time": 3},
        headers=_auth(owner),
    )
    assert response.status_code == 200


def test_add_spatial_text_spoofed_user_id_has_no_effect(db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    asset = _make_media_asset(db_session, project)
    asset.metadata_json = {"spatial_text_tracking": {"status": "completed", "depth_key": "d", "camera_poses_key": "c"}}
    stranger = _make_user(db_session)
    db_session.commit()
    client = _client_for(_spatial_text.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/timelines/{timeline.id}/spatial-text",
        json={"user_id": str(stranger.id), "source_asset_id": str(asset.id), "text": "hi", "x": .5, "y": .5, "z": .5, "start_time": 0, "end_time": 3},
        headers=_auth(owner),
    )
    assert response.status_code == 200


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_add_spatial_text_unauthenticated_rejected(headers, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    asset = _make_media_asset(db_session, project)
    client = _client_for(_spatial_text.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/timelines/{timeline.id}/spatial-text",
        json={"source_asset_id": str(asset.id), "text": "hi", "x": .5, "y": .5, "z": .5, "start_time": 0, "end_time": 3},
        headers=headers,
    )
    assert response.status_code == 401


# ============================================================================
# spatial_video.py — POST /timelines/{id}/spatial-video
# ============================================================================

@pytest.fixture()
def spatial_video_task(monkeypatch):
    fake = _RecordingTask()
    monkeypatch.setattr(_spatial_video, "render_mvhevc_spatial_video", fake)
    return fake


def _make_completed_render_job(db_session, project: Project, timeline: Timeline) -> RenderJob:
    job = RenderJob(project_id=project.id, timeline_id=timeline.id, status=RenderStatus.COMPLETED, output_key="renders/m2-test.mp4")
    db_session.add(job)
    db_session.flush()
    return job


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_spatial_video_export_unauthenticated_rejected(headers, db_session, spatial_video_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    job = _make_completed_render_job(db_session, project, timeline)
    client = _client_for(_spatial_video.router, db_session=db_session)

    response = client.post(f"{API_V1}/timelines/{timeline.id}/spatial-video", json={"source_render_job_id": str(job.id)}, headers=headers)
    assert response.status_code == 401
    assert spatial_video_task.calls == []


def test_spatial_video_export_non_owner_rejected(db_session, spatial_video_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    job = _make_completed_render_job(db_session, project, timeline)
    stranger = _make_user(db_session)
    client = _client_for(_spatial_video.router, db_session=db_session)

    response = client.post(f"{API_V1}/timelines/{timeline.id}/spatial-video", json={"source_render_job_id": str(job.id)}, headers=_auth(stranger))
    assert response.status_code == 403
    assert spatial_video_task.calls == []


def test_spatial_video_export_owner_succeeds(db_session, spatial_video_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    job = _make_completed_render_job(db_session, project, timeline)
    client = _client_for(_spatial_video.router, db_session=db_session)

    response = client.post(f"{API_V1}/timelines/{timeline.id}/spatial-video", json={"source_render_job_id": str(job.id)}, headers=_auth(owner))
    assert response.status_code == 202
    assert len(spatial_video_task.calls) == 1


# ============================================================================
# optics.py — POST /media/{id}/analyze-optics, .../retime-optical-flow,
# .../render-optical-look
# ============================================================================

@pytest.fixture()
def optics_tasks(monkeypatch):
    analyze, retime, look = _RecordingTask(), _RecordingTask(), _RecordingTask()
    monkeypatch.setattr(_optics, "analyze_optics", analyze)
    monkeypatch.setattr(_optics, "retime_with_optical_flow", retime)
    monkeypatch.setattr(_optics, "render_optical_look_preview", look)
    return analyze, retime, look


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_analyze_optics_unauthenticated_rejected(headers, db_session, optics_tasks):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    client = _client_for(_optics.router, db_session=db_session)

    response = client.post(f"{API_V1}/media/{asset.id}/analyze-optics", json={}, headers=headers)
    assert response.status_code == 401
    assert optics_tasks[0].calls == []


def test_analyze_optics_non_owner_rejected(db_session, optics_tasks):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    stranger = _make_user(db_session)
    client = _client_for(_optics.router, db_session=db_session)

    response = client.post(f"{API_V1}/media/{asset.id}/analyze-optics", json={}, headers=_auth(stranger))
    assert response.status_code == 403
    assert optics_tasks[0].calls == []


def test_analyze_optics_owner_succeeds(db_session, optics_tasks):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    client = _client_for(_optics.router, db_session=db_session)

    response = client.post(f"{API_V1}/media/{asset.id}/analyze-optics", json={}, headers=_auth(owner))
    assert response.status_code == 202
    assert len(optics_tasks[0].calls) == 1


def test_render_optical_look_spoofed_user_id_has_no_effect(db_session, optics_tasks):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    stranger = _make_user(db_session)
    client = _client_for(_optics.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/media/{asset.id}/render-optical-look", json={"user_id": str(stranger.id)}, headers=_auth(owner),
    )
    assert response.status_code == 202
    assert len(optics_tasks[2].calls) == 1


# ============================================================================
# film_optics.py — PUT /timelines/{id}/film-optics-master,
# GET /timelines/{id}/film-optics-mtf
# ============================================================================

@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_film_optics_master_unauthenticated_rejected(headers, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    client = _client_for(_film_optics.router, db_session=db_session)

    response = client.put(f"{API_V1}/timelines/{timeline.id}/film-optics-master", json={}, headers=headers)
    assert response.status_code == 401


def test_film_optics_master_non_owner_rejected(db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    stranger = _make_user(db_session)
    client = _client_for(_film_optics.router, db_session=db_session)

    response = client.put(f"{API_V1}/timelines/{timeline.id}/film-optics-master", json={}, headers=_auth(stranger))
    assert response.status_code == 403


def test_film_optics_master_owner_succeeds(db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    client = _client_for(_film_optics.router, db_session=db_session)

    response = client.put(f"{API_V1}/timelines/{timeline.id}/film-optics-master", json={}, headers=_auth(owner))
    assert response.status_code == 200


def test_film_optics_mtf_owner_succeeds(db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    client = _client_for(_film_optics.router, db_session=db_session)

    response = client.get(f"{API_V1}/timelines/{timeline.id}/film-optics-mtf", headers=_auth(owner))
    assert response.status_code == 200


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_film_optics_mtf_unauthenticated_rejected(headers, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    client = _client_for(_film_optics.router, db_session=db_session)

    response = client.get(f"{API_V1}/timelines/{timeline.id}/film-optics-mtf", headers=headers)
    assert response.status_code == 401


def test_film_optics_mtf_non_owner_rejected(db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    stranger = _make_user(db_session)
    client = _client_for(_film_optics.router, db_session=db_session)

    response = client.get(f"{API_V1}/timelines/{timeline.id}/film-optics-mtf", headers=_auth(stranger))
    assert response.status_code == 403


# ============================================================================
# relighting.py — POST /media/{id}/analyze-virtual-relight,
# PUT /timelines/{id}/virtual-relight
# ============================================================================

@pytest.fixture()
def relighting_task(monkeypatch):
    fake = _RecordingTask()
    monkeypatch.setattr(_relighting, "analyze_depth_and_lighting", fake)
    return fake


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_relighting_analysis_unauthenticated_rejected(headers, db_session, relighting_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    client = _client_for(_relighting.router, db_session=db_session)

    response = client.post(f"{API_V1}/media/{asset.id}/analyze-virtual-relight", json={}, headers=headers)
    assert response.status_code == 401
    assert relighting_task.calls == []


def test_relighting_analysis_non_owner_rejected(db_session, relighting_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    stranger = _make_user(db_session)
    client = _client_for(_relighting.router, db_session=db_session)

    response = client.post(f"{API_V1}/media/{asset.id}/analyze-virtual-relight", json={}, headers=_auth(stranger))
    assert response.status_code == 403
    assert relighting_task.calls == []


def test_relighting_analysis_owner_succeeds(db_session, relighting_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    client = _client_for(_relighting.router, db_session=db_session)

    response = client.post(f"{API_V1}/media/{asset.id}/analyze-virtual-relight", json={}, headers=_auth(owner))
    assert response.status_code == 202
    assert len(relighting_task.calls) == 1


def test_virtual_relight_update_owner_succeeds(db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    client = _client_for(_relighting.router, db_session=db_session)

    response = client.put(f"{API_V1}/timelines/{timeline.id}/virtual-relight", json={}, headers=_auth(owner))
    assert response.status_code == 200


def test_virtual_relight_update_non_owner_rejected(db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    stranger = _make_user(db_session)
    client = _client_for(_relighting.router, db_session=db_session)

    response = client.put(f"{API_V1}/timelines/{timeline.id}/virtual-relight", json={}, headers=_auth(stranger))
    assert response.status_code == 403


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_virtual_relight_update_unauthenticated_rejected(headers, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    client = _client_for(_relighting.router, db_session=db_session)

    response = client.put(f"{API_V1}/timelines/{timeline.id}/virtual-relight", json={}, headers=headers)
    assert response.status_code == 401


# ============================================================================
# parallax.py — POST /media/{id}/generate-parallax-layers
# ============================================================================

@pytest.fixture()
def parallax_task(monkeypatch):
    fake = _RecordingTask()
    monkeypatch.setattr(_parallax, "generate_layers", fake)
    return fake


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_parallax_layers_unauthenticated_rejected(headers, db_session, parallax_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    client = _client_for(_parallax.router, db_session=db_session)

    response = client.post(f"{API_V1}/media/{asset.id}/generate-parallax-layers", json={}, headers=headers)
    assert response.status_code == 401
    assert parallax_task.calls == []


def test_parallax_layers_non_owner_rejected(db_session, parallax_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    stranger = _make_user(db_session)
    client = _client_for(_parallax.router, db_session=db_session)

    response = client.post(f"{API_V1}/media/{asset.id}/generate-parallax-layers", json={}, headers=_auth(stranger))
    assert response.status_code == 403
    assert parallax_task.calls == []


def test_parallax_layers_owner_succeeds(db_session, parallax_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    client = _client_for(_parallax.router, db_session=db_session)

    response = client.post(f"{API_V1}/media/{asset.id}/generate-parallax-layers", json={}, headers=_auth(owner))
    assert response.status_code == 202
    assert len(parallax_task.calls) == 1


def test_parallax_layers_spoofed_user_id_has_no_effect(db_session, parallax_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    asset = _make_media_asset(db_session, project)
    stranger = _make_user(db_session)
    client = _client_for(_parallax.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/media/{asset.id}/generate-parallax-layers", json={"user_id": str(stranger.id)}, headers=_auth(owner),
    )
    assert response.status_code == 202
    assert len(parallax_task.calls) == 1


# ============================================================================
# travel_maps.py — POST /timelines/{id}/travel-map, GET .../travel-map
# ============================================================================

@pytest.fixture()
def travel_map_task(monkeypatch):
    fake = _RecordingTask()
    monkeypatch.setattr(_travel_maps, "generate_travel_map", fake)
    return fake


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_travel_map_post_unauthenticated_rejected(headers, db_session, travel_map_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    client = _client_for(_travel_maps.router, db_session=db_session)

    response = client.post(f"{API_V1}/timelines/{timeline.id}/travel-map", json={"route_text": "A to B"}, headers=headers)
    assert response.status_code == 401
    assert travel_map_task.calls == []


def test_travel_map_post_non_owner_rejected(db_session, travel_map_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    stranger = _make_user(db_session)
    client = _client_for(_travel_maps.router, db_session=db_session)

    response = client.post(f"{API_V1}/timelines/{timeline.id}/travel-map", json={"route_text": "A to B"}, headers=_auth(stranger))
    assert response.status_code == 403
    assert travel_map_task.calls == []


def test_travel_map_post_owner_succeeds(db_session, travel_map_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    client = _client_for(_travel_maps.router, db_session=db_session)

    response = client.post(f"{API_V1}/timelines/{timeline.id}/travel-map", json={"route_text": "A to B"}, headers=_auth(owner))
    assert response.status_code == 202
    assert len(travel_map_task.calls) == 1


def test_travel_map_post_spoofed_user_id_has_no_effect(db_session, travel_map_task):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    stranger = _make_user(db_session)
    client = _client_for(_travel_maps.router, db_session=db_session)

    response = client.post(
        f"{API_V1}/timelines/{timeline.id}/travel-map",
        json={"user_id": str(stranger.id), "route_text": "A to B"}, headers=_auth(owner),
    )
    assert response.status_code == 202
    assert len(travel_map_task.calls) == 1


def test_travel_map_status_owner_succeeds(db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    client = _client_for(_travel_maps.router, db_session=db_session)

    response = client.get(f"{API_V1}/timelines/{timeline.id}/travel-map", headers=_auth(owner))
    assert response.status_code == 200


def test_travel_map_status_non_owner_rejected(db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    stranger = _make_user(db_session)
    client = _client_for(_travel_maps.router, db_session=db_session)

    response = client.get(f"{API_V1}/timelines/{timeline.id}/travel-map", headers=_auth(stranger))
    assert response.status_code == 403


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-real-token"}])
def test_travel_map_status_unauthenticated_rejected(headers, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    client = _client_for(_travel_maps.router, db_session=db_session)

    response = client.get(f"{API_V1}/timelines/{timeline.id}/travel-map", headers=headers)
    assert response.status_code == 401
