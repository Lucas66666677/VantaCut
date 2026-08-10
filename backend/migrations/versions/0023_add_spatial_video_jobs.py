"""add verified MV-HEVC spatial-video derivation jobs

Revision ID: 0023_add_spatial_video_jobs
Revises: 0022_add_camera_ingest
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0023_add_spatial_video_jobs"
down_revision = "0022_add_camera_ingest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "spatial_video_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_render_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("options_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("output_key", sa.String(1000)),
        sa.Column("verification_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("error_message", sa.Text()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["timeline_id"], ["timelines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_render_job_id"], ["render_jobs.id"], ondelete="RESTRICT"),
    )
    for column in ("project_id", "timeline_id", "source_render_job_id", "status"):
        op.create_index(f"ix_spatial_video_jobs_{column}", "spatial_video_jobs", [column])


def downgrade() -> None:
    op.drop_table("spatial_video_jobs")
