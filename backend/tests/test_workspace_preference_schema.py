"""Regression coverage for migration 0037 workspace-preference defaults."""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.db.session import engine
from app.models.entities import Project, User, WorkspacePreference


def _default(column: str) -> str | None:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'workspace_preferences' AND column_name = :column"
            ),
            {"column": column},
        ).scalar_one_or_none()


def test_workspace_preference_timestamps_have_server_defaults_in_db():
    for column in ("created_at", "updated_at"):
        default = _default(column)
        assert default is not None
        assert "now()" in default.lower()


def test_workspace_preference_insert_without_explicit_timestamps_succeeds(db_session):
    owner = User(email=f"workspace-schema-{uuid.uuid4().hex[:12]}@example.com", is_active=True)
    db_session.add(owner); db_session.flush()
    project = Project(owner_id=owner.id, name="Workspace preference schema regression")
    db_session.add(project); db_session.flush()
    preference = WorkspacePreference(user_id=owner.id, project_id=project.id, layout_version=1, layout_json={})
    db_session.add(preference); db_session.flush(); db_session.refresh(preference)
    assert preference.created_at is not None
    assert preference.updated_at is not None
