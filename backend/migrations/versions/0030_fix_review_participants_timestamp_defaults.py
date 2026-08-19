"""restore review_participants timestamp server defaults

Revision ID: 0030_fix_review_participants_timestamp_defaults
Revises: 0029_add_user_auth_fields
"""
from alembic import op
import sqlalchemy as sa


revision = "0030_fix_review_participants_timestamp_defaults"
down_revision = "0029_add_user_auth_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0012_add_review_approval.py created review_participants.created_at and
    # .updated_at as `nullable=False` with NO `server_default`, even though
    # the ReviewParticipant ORM model (app/models/entities.py, via
    # TimestampMixin) declares `server_default=func.now()` for both columns
    # — unlike every table in 0001_initial.py, which correctly sets
    # `server_default=sa.func.now()` in its actual DDL. This left the live
    # database schema out of sync with what the ORM expects: any INSERT
    # that (correctly, per the ORM) omits these two columns relies on
    # Postgres to fill them from a DEFAULT clause that was never actually
    # installed, and hits a NotNullViolation instead of succeeding.
    #
    # This is a real, live, production-affecting bug, not a theoretical
    # one: app/api/v1/reviews.py's add_review_participant already
    # constructs `ReviewParticipant(timeline_id=..., user_id=...)` this
    # exact way (no explicit timestamps), so that endpoint would already
    # hit this same failure against a real Postgres database today.
    # Confirmed via a real CI run against a live Postgres service
    # container (backend/tests/test_collaboration.py surfaced it first,
    # since no prior test ever exercised this insert path).
    #
    # This migration only adds the missing DEFAULT clause to the two
    # existing columns. It does not touch existing rows' timestamp values
    # (ALTER COLUMN ... SET DEFAULT has no effect on rows already in the
    # table — it only changes what a future INSERT falls back to when the
    # column is omitted), does not touch any other column or table, and
    # does not edit migration 0012 itself. Fully additive, non-destructive.
    op.alter_column(
        "review_participants",
        "created_at",
        server_default=sa.func.now(),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "review_participants",
        "updated_at",
        server_default=sa.func.now(),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "review_participants",
        "updated_at",
        server_default=None,
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "review_participants",
        "created_at",
        server_default=None,
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
    )
