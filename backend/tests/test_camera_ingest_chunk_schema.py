"""Regression tests for migration 0036 camera_ingest_chunks defaults."""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.db.session import engine
from app.models.entities import CameraDevice, CameraIngestChunk, CameraIngestSession, Project, Timeline, User


def _default(column: str) -> str | None:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'camera_ingest_chunks' AND column_name = :column"
            ),
            {"column": column},
        ).scalar_one_or_none()


def test_camera_ingest_chunk_timestamps_have_server_defaults_in_db():
    for column in ("created_at", "updated_at"):
        default = _default(column)
        assert default is not None
        assert "now()" in default.lower()


def test_camera_ingest_chunk_insert_without_explicit_timestamps_succeeds(db_session):
    """Mirrors both camera chunk upload routes, which omit these timestamps."""
    owner = User(email=f"chunk-schema-{uuid.uuid4().hex[:12]}@example.com", is_active=True)
    db_session.add(owner)
    db_session.flush()
    project = Project(owner_id=owner.id, name="Camera chunk schema regression")
    db_session.add(project)
    db_session.flush()
    timeline = Timeline(project_id=project.id, name="Camera chunk schema timeline", is_current=True)
    device = CameraDevice(
        project_id=project.id, device_identifier=f"camera-{uuid.uuid4().hex}",
        display_name="Schema test camera", encrypted_hmac_secret="test",
    )
    db_session.add_all([timeline, device])
    db_session.flush()
    session = CameraIngestSession(
        project_id=project.id, device_id=device.id, timeline_id=timeline.id,
        capture_id=f"capture-{uuid.uuid4().hex}",
    )
    db_session.add(session)
    db_session.flush()
    chunk = CameraIngestChunk(
        session_id=session.id, sequence_number=1, storage_key=f"chunks/{uuid.uuid4().hex}.mp4",
        content_sha256="a" * 64, size_bytes=1,
    )
    db_session.add(chunk)
    db_session.flush()
    db_session.refresh(chunk)
    assert chunk.created_at is not None
    assert chunk.updated_at is not None
