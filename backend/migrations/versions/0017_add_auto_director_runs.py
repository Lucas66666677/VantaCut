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


auto_director_status = sa.Enum(
    "queued", "scripting", "researching", "narrating", "editing", "ready_for_review", "failed",
    name="auto_director_status",
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
