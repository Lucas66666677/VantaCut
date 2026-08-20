"""Regression tests for migration
0034_fix_spatial_video_jobs_timestamp_defaults.

Background (full detail in that migration's own docstring): spatial_video_jobs
(created by 0023_add_spatial_video_jobs.py) has the identical missing-
`server_default` bug already fixed for review_participants (0030),
marketplace_templates/creator_connect_accounts/compute_nodes (0031),
template_licenses/distributed_render_*/compute_credit_ledger (0032), and
auto_director_runs (0033). It was discovered while auditing the M2
mechanical-migration candidate files, not surfaced only after a CI failure —
app/api/v1/spatial_video.py's request_spatial_video_export already
constructs SpatialVideoJob(...) without explicit timestamps, so this is a
live production bug independent of the identity-migration work in this PR.

Like the earlier schema-regression suites, these tests query
`information_schema` against the CI-migrated database (a real
`alembic upgrade head` run), not a `Base.metadata.create_all()` fallback,
since only the former exercises the previously-broken migration path.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.db.session import engine
from app.models.entities import (
    Project,
    RenderJob,
    RenderStatus,
    SpatialVideoJob,
    Timeline,
    User,
)


def _column_default(table: str, column: str) -> str | None:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        ).scalar_one_or_none()


def _assert_has_now_default(table: str, column: str) -> None:
    default = _column_default(table, column)
    assert default is not None, (
        f"{table}.{column} has no server_default in the actual database — "
        "migration 0034_fix_spatial_video_jobs_timestamp_defaults did not "
        "apply, or `alembic upgrade head` was not run before this test suite."
    )
    assert "now()" in default.lower()


def _make_user(db_session) -> User:
    user = User(email=f"schema-fix4-{uuid.uuid4().hex[:12]}@example.com", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session, owner: User) -> Project:
    project = Project(owner_id=owner.id, name="Schema fix 4 regression test project")
    db_session.add(project)
    db_session.flush()
    return project


def _make_timeline(db_session, project: Project) -> Timeline:
    timeline = Timeline(project_id=project.id, name="Schema fix 4 timeline", is_current=True)
    db_session.add(timeline)
    db_session.flush()
    return timeline


def _make_completed_render_job(db_session, project: Project, timeline: Timeline) -> RenderJob:
    job = RenderJob(
        project_id=project.id, timeline_id=timeline.id,
        status=RenderStatus.COMPLETED, output_key="renders/schema-fix4-test.mp4",
    )
    db_session.add(job)
    db_session.flush()
    return job


# --- server_default existence ------------------------------------------------

def test_spatial_video_jobs_timestamps_have_server_default_in_db():
    _assert_has_now_default("spatial_video_jobs", "created_at")
    _assert_has_now_default("spatial_video_jobs", "updated_at")


# --- production-style insert without explicit timestamps succeeds -----------

def test_spatial_video_job_insert_without_explicit_timestamps_succeeds(db_session):
    """Mirrors app/api/v1/spatial_video.py's request_spatial_video_export,
    which constructs SpatialVideoJob(...) without explicit timestamps."""
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    source = _make_completed_render_job(db_session, project, timeline)

    job = SpatialVideoJob(
        project_id=project.id,
        timeline_id=timeline.id,
        source_render_job_id=source.id,
        options_json={},
    )
    db_session.add(job)
    db_session.flush()  # would previously raise sqlalchemy.exc.IntegrityError (NotNullViolation)

    db_session.refresh(job)
    assert job.created_at is not None
    assert job.updated_at is not None
