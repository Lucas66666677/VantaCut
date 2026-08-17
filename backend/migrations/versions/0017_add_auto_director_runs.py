"""add persistent autonomous documentary runs

Revision ID: 0017_add_auto_director_runs
Revises: 0016_add_relighting_analysis_type
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0017_add_auto_director_runs"
down_revision = "0016_add_relighting_analysis_type"
branch_labels = None
depends_on = None


# postgresql.ENUM (not sa.Enum) with create_type=False: a plain sa.Enum's
# create_type flag is dropped when SQLAlchemy adapts it to the native PG
# ENUM for column-level DDL (adapt_emulated_to_native() only carries
# create_type over when the source type is already NativeForEmulated), so
# op.create_table() below would silently re-CREATE TYPE and collide with
# the explicit .create() call. See migrations/env.py and 0001_initial.py /
# 0028_add_distributed_compute.py for the same fix.
auto_director_status = postgresql.ENUM(
    "queued", "scripting", "researching", "narrating", "editing", "ready_for_review", "failed",
    name="auto_director_status",
    create_type=False,
)


def upgrade() -> None:
    auto_director_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "auto_director_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("creative_brief_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("status", auto_director_status, nullable=False, server_default="queued"),
        sa.Column("provider_name", sa.String(length=120), nullable=True),
        sa.Column("script_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("research_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("narration_key", sa.String(length=1000), nullable=True),
        sa.Column("result_timeline_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["result_timeline_id"], ["timelines.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_auto_director_runs_project_id", "auto_director_runs", ["project_id"])
    op.create_index("ix_auto_director_runs_requested_by_id", "auto_director_runs", ["requested_by_id"])
    op.create_index("ix_auto_director_runs_status", "auto_director_runs", ["status"])
    op.create_index("ix_auto_director_runs_result_timeline_id", "auto_director_runs", ["result_timeline_id"])


def downgrade() -> None:
    op.drop_table("auto_director_runs")
    auto_director_status.drop(op.get_bind(), checkfirst=True)
