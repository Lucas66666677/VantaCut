"""Regression tests for migration
0033_fix_auto_director_runs_timestamp_defaults.

Background (full detail in that migration's own docstring): auto_director_runs
(created by 0017_add_auto_director_runs.py) has the identical missing-
`server_default` bug already fixed for review_participants (0030),
marketplace_templates/creator_connect_accounts/compute_nodes (0031), and
template_licenses/distributed_render_*/compute_credit_ledger (0032). It was
surfaced by real CI — PR #8's tests/test_auto_editing_identity.py is the
first test suite to actually INSERT a row into auto_director_runs against a
real, migrated Postgres database, and that insert failed with
sqlalchemy.exc.IntegrityError (NotNullViolation) before this fix.
app/api/v1/auto_director.py's start_auto_director already constructs
AutoDirectorRun(...) without explicit timestamps, so this is a live
production bug, not a test-only artifact.

Like test_review_participant_schema.py, test_marketplace_and_compute_schema.py,
and test_distributed_render_and_license_schema.py, these tests query
`information_schema` against the CI-migrated database (a real
`alembic upgrade head` run), not a `Base.metadata.create_all()` fallback,
since only the former exercises the previously-broken migration path.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.db.session import engine
from app.models.entities import AutoDirectorRun, AutoDirectorStatus, Project, User


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
        "migration 0033_fix_auto_director_runs_timestamp_defaults did not "
        "apply, or `alembic upgrade head` was not run before this test suite."
    )
    assert "now()" in default.lower()


def _make_user(db_session) -> User:
    user = User(email=f"schema-fix3-{uuid.uuid4().hex[:12]}@example.com", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session, owner: User) -> Project:
    project = Project(owner_id=owner.id, name="Schema fix 3 regression test project")
    db_session.add(project)
    db_session.flush()
    return project


# --- server_default existence ------------------------------------------------

def test_auto_director_runs_timestamps_have_server_default_in_db():
    _assert_has_now_default("auto_director_runs", "created_at")
    _assert_has_now_default("auto_director_runs", "updated_at")


# --- production-style insert without explicit timestamps succeeds -----------

def test_auto_director_run_insert_without_explicit_timestamps_succeeds(db_session):
    """Mirrors app/api/v1/auto_director.py's start_auto_director, which
    constructs AutoDirectorRun(...) without explicit timestamps."""
    requester = _make_user(db_session)
    project = _make_project(db_session, requester)

    run = AutoDirectorRun(
        project_id=project.id,
        requested_by_id=requester.id,
        topic="Schema fix 3 regression test topic",
        creative_brief_json={},
        status=AutoDirectorStatus.QUEUED,
    )
    db_session.add(run)
    db_session.flush()  # would previously raise sqlalchemy.exc.IntegrityError (NotNullViolation)

    db_session.refresh(run)
    assert run.created_at is not None
    assert run.updated_at is not None
