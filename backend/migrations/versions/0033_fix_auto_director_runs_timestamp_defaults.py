"""restore auto_director_runs timestamp server defaults

Revision ID: 0033_fix_auto_director_runs_timestamp_defaults
Revises: 0032_fix_distributed_render_and_license_timestamp_defaults
"""
from alembic import op
import sqlalchemy as sa


revision = "0033_fix_auto_director_runs_timestamp_defaults"
down_revision = "0032_fix_distributed_render_and_license_timestamp_defaults"
branch_labels = None
depends_on = None


# Same bug class as 0030/0031/0032, this time for auto_director_runs.
# Surfaced by real CI, not a hypothetical audit: PR #8 (the M1 mechanical
# auth migration) added tests/test_auto_editing_identity.py, which is the
# first test suite ever to actually INSERT a row into auto_director_runs
# against a real, migrated Postgres database. That real insert failed with:
#
#   sqlalchemy.exc.IntegrityError: (psycopg.errors.NotNullViolation)
#   null value in column "created_at" of relation "auto_director_runs"
#   violates not-null constraint
#
# Root cause, proven by reading both sides directly (same method as
# 0030/0031/0032, not pattern-matching on table name):
#
#   1. ORM: AutoDirectorRun (app/models/entities.py) inherits
#      TimestampMixin (app/models/base.py), which declares
#      `server_default=func.now()` for both created_at and updated_at.
#   2. DB DDL: 0017_add_auto_director_runs.py's op.create_table() call
#      creates created_at/updated_at as `nullable=False` with NO
#      `server_default` argument — unlike creative_brief_json, status,
#      script_json, and research_json in that same op.create_table() call,
#      which correctly do pass `server_default=...`. No later migration
#      touches auto_director_runs' timestamp columns either (confirmed by
#      searching every migration file 0018-0032).
#   3. Production insertion path: app/api/v1/auto_director.py's
#      start_auto_director already constructs AutoDirectorRun(...) without
#      explicit timestamps, trusting the (previously nonexistent) database
#      default — so this is not a test-only artifact, it is a live bug that
#      would hit any real request to POST /auto-director today.
#
# Same fix, same safety properties as 0030/0031/0032: additive only.
# ALTER COLUMN ... SET DEFAULT has no effect on rows already in the table —
# it only changes what a future INSERT falls back to when the column is
# omitted. No existing data is touched, no other column or table is
# touched, and 0017 is not edited.
_TABLE = "auto_director_runs"


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
