"""Priority security fix: POST /compute/render-jobs/{id}/offload
(app/api/v1/distributed_compute.py::offload_render_job).

This route had NO verified caller identity at all — it accepted a
client-supplied `owner_id` in the request body and trusted it outright for
the "only the project owner can decentralize this render" check inside
app/services/distributed_compute.py's create_batch(). Any caller could
supply any project owner's id and have a QUEUED render of that project
moved into the distributed compute pool.

This is an END-USER control-plane action (the owner of a QUEUED
centralized render choosing to decentralize it), not part of the
compute-node protocol: the node/worker side of that protocol
(heartbeat, assignment fetch, signed chunk-result submission) is
authenticated by an Ed25519 keypair verified in
verify_node_signature/verify_ticket, which this fix does not touch.
test_distributed_compute.py::test_heartbeat_still_requires_node_signature
(unchanged by this fix) already proves that protocol still rejects a bad
node signature; this file does not duplicate that coverage.

Identity (get_current_user) and the ownership check run for real against
the test database, never mocked, matching every other Batch 1/2A/2B/M1/M2
security test file in this program. The only genuine external boundary in
this route — none, in fact: offload_render_job does not call Celery or any
other infrastructure boundary, so nothing needs mocking here at all.
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
from app.models.entities import DistributedRenderBatch, Project, RenderJob, RenderStatus, Timeline, User

API_V1 = "/api/v1"


def _load_distributed_compute_router():
    """Load app/api/v1/distributed_compute.py directly, bypassing
    app.api.__init__ — same reasoning and same module already used by
    test_distributed_compute.py."""
    module_path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "distributed_compute.py"
    spec = importlib.util.spec_from_file_location("_vantacut_test_render_offload_router", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_compute_module = _load_distributed_compute_router()


@pytest.fixture()
def app_client(db_session):
    app = FastAPI()
    app.include_router(_compute_module.router, prefix=API_V1)

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


def _make_user(db_session) -> User:
    user = User(email=f"render-offload-{uuid.uuid4().hex[:12]}@example.com", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session, owner: User) -> Project:
    project = Project(owner_id=owner.id, name="Render offload identity test project")
    db_session.add(project)
    db_session.flush()
    return project


def _make_timeline(db_session, project: Project) -> Timeline:
    # A single chunkable main_video clip — enough for build_chunk_manifests
    # (app/services/distributed_compute.py) to produce a non-empty chunk
    # plan without tripping any of its cross-chunk-effect guards.
    timeline = Timeline(
        project_id=project.id,
        name="Render offload identity test timeline",
        settings_json={
            "confirmed_timeline": {
                "tracks": [
                    {"type": "main_video", "clips": [{"action": "keep", "source_start": 0.0, "source_end": 30.0}]}
                ]
            }
        },
    )
    db_session.add(timeline)
    db_session.flush()
    return timeline


def _make_render_job(db_session, project: Project, timeline: Timeline, status: RenderStatus = RenderStatus.QUEUED) -> RenderJob:
    job = RenderJob(project_id=project.id, timeline_id=timeline.id, status=status)
    db_session.add(job)
    db_session.flush()
    return job


def _auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _offload_body(**overrides) -> dict:
    body = {"chunk_seconds": 5, "replication_factor": 2, "resolution": "4k", "container_format": "mp4"}
    body.update(overrides)
    return body


def _offload(app_client, render_job_id, headers=None, **body_overrides):
    return app_client.post(
        f"{API_V1}/compute/render-jobs/{render_job_id}/offload",
        json=_offload_body(**body_overrides),
        headers=headers or {},
    )


def test_anonymous_rejected(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    job = _make_render_job(db_session, project, timeline)

    response = _offload(app_client, job.id)
    assert response.status_code == 401
    assert db_session.query(DistributedRenderBatch).filter_by(render_job_id=job.id).first() is None


def test_invalid_token_rejected(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    job = _make_render_job(db_session, project, timeline)

    response = _offload(app_client, job.id, headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401
    assert db_session.query(DistributedRenderBatch).filter_by(render_job_id=job.id).first() is None


def test_authenticated_non_owner_rejected_and_causes_zero_side_effects(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    job = _make_render_job(db_session, project, timeline)
    stranger = _make_user(db_session)

    response = _offload(app_client, job.id, headers=_auth(stranger))
    assert response.status_code == 403

    # Zero distributed-compute side effects from the unauthorized call:
    # no batch created, and the render job's status/progress are untouched.
    assert db_session.query(DistributedRenderBatch).filter_by(render_job_id=job.id).first() is None
    db_session.refresh(job)
    assert job.status == RenderStatus.QUEUED
    assert job.progress == 0


def test_spoofed_legacy_owner_id_has_no_effect(app_client, db_session):
    """The request schema no longer accepts an owner_id field at all — a
    non-owner who supplies the real owner's id in the request body is
    still rejected, because the caller's identity now comes exclusively
    from the verified bearer token, never the request body."""
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    job = _make_render_job(db_session, project, timeline)
    attacker = _make_user(db_session)

    response = _offload(app_client, job.id, headers=_auth(attacker), owner_id=str(owner.id))
    assert response.status_code == 403
    assert db_session.query(DistributedRenderBatch).filter_by(render_job_id=job.id).first() is None


def test_rightful_owner_succeeds(app_client, db_session):
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    job = _make_render_job(db_session, project, timeline)

    response = _offload(app_client, job.id, headers=_auth(owner))
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["chunk_count"] >= 1

    batch = db_session.query(DistributedRenderBatch).filter_by(render_job_id=job.id).first()
    assert batch is not None
    assert batch.owner_id == owner.id
    assert batch.project_id == project.id


def test_unknown_render_job_returns_404_regardless_of_auth(app_client, db_session):
    """Non-enumerating: a caller with a valid token but a made-up
    render_job_id gets the same 404 an owner would get for a render job
    that genuinely doesn't exist — no distinct response leaks whether the
    id is real but owned by someone else."""
    caller = _make_user(db_session)

    response = _offload(app_client, uuid.uuid4(), headers=_auth(caller))
    assert response.status_code == 404


def test_non_queued_render_job_rejected_after_ownership_passes(app_client, db_session):
    """Ownership is checked before the pre-existing QUEUED-only business
    rule, but a non-owner must never learn the job's status: this proves
    the rightful owner reaches the pre-existing 409, unchanged."""
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    job = _make_render_job(db_session, project, timeline, status=RenderStatus.COMPLETED)

    response = _offload(app_client, job.id, headers=_auth(owner))
    assert response.status_code == 409
    assert db_session.query(DistributedRenderBatch).filter_by(render_job_id=job.id).first() is None
