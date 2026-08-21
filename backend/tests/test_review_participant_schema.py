"""Regression tests for migration 0030_fix_review_participants_timestamp_defaults.

Background (full detail in that migration's own docstring): migration
0012_add_review_approval.py created `review_participants.created_at`/
`.updated_at` as `nullable=False` with no `server_default`, even though the
`ReviewParticipant` ORM model declares one via `TimestampMixin`. That gap
meant the exact insert pattern production code already uses —
app/api/v1/reviews.py's `add_review_participant`, which constructs
`ReviewParticipant(timeline_id=..., user_id=...)` without supplying
timestamps, trusting the (previously nonexistent) database default — hit a
real `NotNullViolation` against Postgres. Batch 2A's own
test_collaboration.py first surfaced this by accident; this file proves the
schema fix (not a test-side workaround) actually resolves it.

These tests run against whatever schema CI's own `alembic upgrade head`
step already produced (see conftest.py's `_ensure_schema`, which is a
`Base.metadata.create_all()` no-op fallback for local runs only — it would
NOT reproduce this bug, since `create_all()` derives DDL straight from the
ORM's own declared `server_default`, bypassing the missing-migration gap
entirely; only a real `alembic upgrade head` run exercises the real,
previously-broken migration path). That's why `test_alembic_version_column_width.py`'s
same pattern — querying `information_schema` against the CI-migrated
database, not re-running Alembic inside the test — is reused here.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from sqlalchemy import text

from app.db.session import engine
from app.models.entities import Project, ReviewParticipant, ReviewRole, Timeline, User


def _load_reviews_module():
    """Load app/api/v1/reviews.py directly, bypassing `app.api.__init__` —
    same reasoning as tests/conftest.py::_load_auth_router and
    test_collaboration.py::_load_collaboration_router: a normal
    `from app.api.v1.reviews import _timeline_for_user` would force Python
    to import the `app.api`/`app.api.v1` packages first, which eagerly
    imports all ~75 v1 routers, some of which require heavy dependencies
    (torch, mediapipe, ...) not installed in this narrow CI test slice.
    reviews.py itself only imports app.db.session, app.models.entities,
    app.schemas.review (pydantic only), and app.services.review_exports
    (stdlib csv/io/json only) — all already available.
    """
    module_path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "reviews.py"
    spec = importlib.util.spec_from_file_location("_vantacut_test_reviews_module", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_reviews_module = _load_reviews_module()


def _column_default(table: str, column: str) -> str | None:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        ).scalar_one_or_none()


def _make_user(db_session) -> User:
    user = User(email=f"schema-fix-{uuid.uuid4().hex[:12]}@example.com", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session, owner: User) -> Project:
    project = Project(owner_id=owner.id, name="Schema fix regression test project")
    db_session.add(project)
    db_session.flush()
    return project


def _make_timeline(db_session, project: Project) -> Timeline:
    timeline = Timeline(project_id=project.id, name="Schema fix regression test timeline")
    db_session.add(timeline)
    db_session.flush()
    return timeline


def test_review_participants_created_at_has_server_default_in_db():
    default = _column_default("review_participants", "created_at")
    assert default is not None, (
        "review_participants.created_at has no server_default in the actual "
        "database — migration 0030_fix_review_participants_timestamp_defaults "
        "did not apply, or `alembic upgrade head` was not run before this "
        "test suite."
    )
    assert "now()" in default.lower()


def test_review_participants_updated_at_has_server_default_in_db():
    default = _column_default("review_participants", "updated_at")
    assert default is not None, (
        "review_participants.updated_at has no server_default in the actual "
        "database — migration 0030_fix_review_participants_timestamp_defaults "
        "did not apply, or `alembic upgrade head` was not run before this "
        "test suite."
    )
    assert "now()" in default.lower()


def test_review_participant_insert_without_explicit_timestamps_succeeds(db_session):
    """Proves the real production pattern (app/api/v1/reviews.py's
    add_review_participant: `ReviewParticipant(timeline_id=..., user_id=...)`,
    no timestamps supplied) now succeeds against a real database, instead of
    raising NotNullViolation. This is the exact insert Batch 2A's own test
    previously had to work around with explicit timestamps — that workaround
    is gone (see test_collaboration.py's current
    test_ws_non_owner_review_participant_rejected, which still sets
    timestamps only because collaboration.py no longer treats
    ReviewParticipant as an authorization grant at all, not because this
    schema bug is unfixed)."""
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    reviewer = _make_user(db_session)

    participant = ReviewParticipant(timeline_id=timeline.id, user_id=reviewer.id, role=ReviewRole.REVIEWER)
    db_session.add(participant)
    db_session.flush()  # would previously raise sqlalchemy.exc.IntegrityError (NotNullViolation)

    db_session.refresh(participant)
    assert participant.created_at is not None
    assert participant.updated_at is not None


def test_review_participant_satisfies_reviews_timeline_for_user(db_session):
    """Proves the schema fix actually unblocks ReviewParticipant's real,
    intended use — app/api/v1/reviews.py's `_timeline_for_user`, which grants
    review-comment and approve/reject access to a timeline's owner or its
    ReviewParticipants. (Not collaboration.py's WebSocket: that route was
    corrected this session to owner-only after a focused review found it
    granted live-editing capability ReviewParticipant was never evidenced to
    carry — see collaboration.py's `_authorize_timeline_access` docstring.)"""
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    reviewer = _make_user(db_session)

    participant = ReviewParticipant(timeline_id=timeline.id, user_id=reviewer.id, role=ReviewRole.REVIEWER)
    db_session.add(participant)
    db_session.flush()

    resolved_timeline, role = _reviews_module._timeline_for_user(db_session, timeline.id, reviewer)
    assert resolved_timeline.id == timeline.id
    assert role == ReviewRole.REVIEWER.value
