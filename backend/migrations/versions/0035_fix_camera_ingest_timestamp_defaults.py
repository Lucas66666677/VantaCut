"""restore camera_devices and camera_ingest_sessions timestamp server defaults

Revision ID: 0035_fix_camera_ingest_timestamp_defaults
Revises: 0034_fix_spatial_video_jobs_timestamp_defaults
"""
from alembic import op
import sqlalchemy as sa


revision = "0035_fix_camera_ingest_timestamp_defaults"
down_revision = "0034_fix_spatial_video_jobs_timestamp_defaults"
branch_labels = None
depends_on = None


# Same bug class as 0030/0031/0032/0033/0034. Discovered while auditing the
# timestamp-default status of the two tables directly INSERTed by the four
# routes fixed in this PR (register_camera_device, start_camera_ingest_session
# in app/api/v1/camera_ingest.py, and create_pairing in
# app/api/v1/wireless_cameras.py — list_pairings performs no insert).
#
# Root cause, proven the same way as the prior five instances (ORM + historical
# migration DDL + real production insert path, not pattern-matching on table
# name):
#
#   1. ORM: CameraDevice and CameraIngestSession (app/models/entities.py) both
#      inherit TimestampMixin (app/models/base.py), which declares
#      `server_default=func.now()` for both created_at and updated_at.
#   2. DB DDL: 0022_add_camera_ingest.py defines a shared `_timestamps()`
#      helper used by all three tables it creates (camera_devices,
#      camera_ingest_sessions, camera_ingest_chunks) that emits created_at
#      and updated_at as `nullable=False` with NO `server_default` argument —
#      unlike device_type, is_active, status, and total_duration_seconds in
#      those same op.create_table() calls, which correctly do pass
#      `server_default=...`. No later migration touches these tables'
#      timestamp columns (confirmed by searching every migration file
#      0023-0034).
#   3. Production insertion path: camera_ingest.py's register_camera_device
#      constructs CameraDevice(project_id=..., device_identifier=..., ...)
#      and start_camera_ingest_session constructs
#      CameraIngestSession(project_id=..., device_id=..., ...); both, plus
#      wireless_cameras.py's create_pairing (which inserts one row into each
#      table), omit created_at/updated_at entirely and call db.add(...) /
#      db.commit() directly — trusting the (previously nonexistent) database
#      default. This is not a test-only artifact: it is a live bug that would
#      hit any real request to these three routes today, independent of the
#      identity-migration work in this PR.
#
# Scope note: camera_ingest_chunks shares the exact same _timestamps() helper
# and therefore the same missing-server_default gap, but it is inserted only
# by upload_camera_chunk / upload_recording_chunk — device/capability-token
# protocol routes that this PR does not touch (see PR description). Fixing it
# is out of this PR's diff boundary; it remains a known, tracked, separate
# instance for a future pass (see vantacut-auth-route-map.md).
#
# Same fix, same safety properties as 0030-0034: additive only.
# ALTER COLUMN ... SET DEFAULT has no effect on rows already in the table —
# it only changes what a future INSERT falls back to when the column is
# omitted. No existing data is touched, no other column or table is touched,
# and 0022 is not edited.
_TABLES = ("camera_devices", "camera_ingest_sessions")


def upgrade() -> None:
    for table in _TABLES:
        op.alter_column(
            table, "created_at",
            server_default=sa.func.now(),
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )
        op.alter_column(
            table, "updated_at",
            server_default=sa.func.now(),
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.alter_column(
            table, "updated_at",
            server_default=None,
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )
        op.alter_column(
            table, "created_at",
            server_default=None,
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )
