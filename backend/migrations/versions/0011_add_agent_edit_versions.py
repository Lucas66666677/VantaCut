"""add agent edit runs and immutable timeline parent links

Revision ID: 0011_add_agent_edit_versions
Revises: 0010_add_multicam_track_type
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0011_add_agent_edit_versions"
down_revision = "0010_add_multicam_track_type"
branch_labels = None
depends_on = None


agent_edit_status = sa.Enum(
    "queued", "planning", "applying", "completed", "failed",
    name="agent_edit_status",
)


def upgrade() -> None:
    agent_edit_status.create(op.get_bind(), checkfirst=True)
    op.add_column("timelines", sa.Column("parent_timeline_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_timelines_parent_timeline_id", "timelines", "timelines",
        ["parent_timeline_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_timelines_parent_timeline_id", "timelines", ["parent_timeline_id"])
    op.create_table(
        "agent_edit_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_timeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("result_timeline_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("status", agent_edit_status, nullable=False, server_default="queued"),
        sa.Column("provider_name", sa.String(length=120), nullable=True),
        sa.Column("tool_calls_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_timeline_id"], ["timelines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["result_timeline_id"], ["timelines.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_agent_edit_runs_project_id", "agent_edit_runs", ["project_id"])
    op.create_index("ix_agent_edit_runs_source_timeline_id", "agent_edit_runs", ["source_timeline_id"])
    op.create_index("ix_agent_edit_runs_result_timeline_id", "agent_edit_runs", ["result_timeline_id"])
    op.create_index("ix_agent_edit_runs_status", "agent_edit_runs", ["status"])


def downgrade() -> None:
    op.drop_table("agent_edit_runs")
    op.drop_index("ix_timelines_parent_timeline_id", table_name="timelines")
    op.drop_constraint("fk_timelines_parent_timeline_id", "timelines", type_="foreignkey")
    op.drop_column("timelines", "parent_timeline_id")
    agent_edit_status.drop(op.get_bind(), checkfirst=True)
