"""restore spatial_video_jobs timestamp server defaults

Revision ID: 0034_fix_spatial_video_jobs_timestamp_defaults
Revises: 0033_fix_auto_director_runs_timestamp_defaults
"""
from alembic import op
import sqlalchemy as sa


revision = "0034_fix_spatial_video_jobs_timestamp_defaults"
down_revision = "0033_fix_auto_director_runs_timestamp_defaults"
branch_labels = None
depends_on = None


# Same bug class as 0030/0031/0032/0033, this time for spatial_video_jobs.
# Discovered while auditing the M2 mechanical-migration candidate files
# (spatial.py, spatial_text.py, spatial_video.py, optics.py, film_optics.py,
# relighting.py, parallax.py, travel_maps.py) for the same identity work —
# spatial_video_jobs is the only table any of these eight route families
# writes to directly, so it was checked against the established bug class
# before M2's own test suite could surface it as a live CI failure the way
# 0033 was.
#
# Root cause, proven the same way as 0030/0031/0032/0033 (ORM + historical
# migration DDL + real production insert path, not pattern-matching on
# table name):
#
#   1. ORM: SpatialVideoJob (app/models/entities.py) inherits
#      TimestampMixin (app/models/base.py), which declares
#      `server_default=func.now()` for both created_at and updated_at.
#   2. DB DDL: 0023_add_spatial_video_jobs.py's op.create_table() call
#      creates created_at/updated_at as `nullable=False` with NO
#      `server_default` argument — unlike status, progress, options_json,
#      and verification_json in that same op.create_table() call, which
#      correctly do pass `server_default=...`. No later migration touches
#      spatial_video_jobs' timestamp columns either (confirmed by searching
#      every migration file 0024-0033).
#   3. Production insertion path: app/api/v1/spatial_video.py's
#      request_spatial_video_export (migrated to get_current_user as part
#      of M2) constructs SpatialVideoJob(project_id=..., timeline_id=...,
#      source_render_job_id=..., options_json=...) with no explicit
#      timestamps, then calls db.add(job); db.commit() directly — trusting
#      the (previously nonexistent) database default. This is not a
#      test-only artifact: it is a live bug that would hit any real request
#      to POST /timelines/{timeline_id}/spatial-video today, independent of
#      the identity-migration work in this PR.
#
# Same fix, same safety properties as 0030/0031/0032/0033: additive only.
# ALTER COLUMN ... SET DEFAULT has no effect on rows already in the table —
# it only changes what a future INSERT falls back to when the column is
# omitted. No existing data is touched, no other column or table is
# touched, and 0023 is not edited.
_TABLE = "spatial_video_jobs"


def upgrade() -> None:
    op.alter_column(
        _TABLE, "created_at",
        server_default=sa.func.now(),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        _TABLE, "updated_at",
        server_default=sa.func.now(),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        _TABLE, "updated_at",
        server_default=None,
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        _TABLE, "created_at",
        server_default=None,
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
    )
