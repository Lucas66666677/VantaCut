"""add project/media lifecycle, Glacier hydration and free-tier retention notices

Revision ID: 0025_add_media_asset_lifecycle
Revises: 0024_add_template_marketplace
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0025_add_media_asset_lifecycle"
down_revision = "0024_add_template_marketplace"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True)))
    op.create_index("ix_users_last_login_at", "users", ["last_login_at"])
    op.add_column("projects", sa.Column("lifecycle_state", sa.String(24), nullable=False, server_default="active"))
    op.add_column("projects", sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.add_column("projects", sa.Column("last_accessed_at", sa.DateTime(timezone=True)))
    for column in ("lifecycle_state", "completed_at", "last_accessed_at"):
        op.create_index(f"ix_projects_{column}", "projects", [column])
    op.add_column("media_assets", sa.Column("archive_status", sa.String(32), nullable=False, server_default="hot"))
    op.add_column("media_assets", sa.Column("archive_requested_at", sa.DateTime(timezone=True)))
    op.add_column("media_assets", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.add_column("media_assets", sa.Column("restore_requested_at", sa.DateTime(timezone=True)))
    op.add_column("media_assets", sa.Column("restore_expires_at", sa.DateTime(timezone=True)))
    op.add_column("media_assets", sa.Column("raw_deleted_at", sa.DateTime(timezone=True)))
    for column in ("archive_status", "archive_requested_at", "archived_at", "restore_requested_at", "restore_expires_at", "raw_deleted_at"):
        op.create_index(f"ix_media_assets_{column}", "media_assets", [column])
    op.create_table(
        "media_hydration_jobs", *_timestamps(),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_ready_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "media_hydration_items", *_timestamps(),
        sa.Column("hydration_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("media_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("restore_header", sa.Text()),
        sa.Column("error_message", sa.Text()),
        sa.ForeignKeyConstraint(["hydration_job_id"], ["media_hydration_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("hydration_job_id", "media_asset_id", name="uq_hydration_item_asset"),
    )
    op.create_table(
        "storage_retention_notices", *_timestamps(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("inactive_day_threshold", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("provider_message_id", sa.String(255)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "inactive_day_threshold", name="uq_retention_notice_user_threshold"),
    )
    for table, columns in {
        "media_hydration_jobs": ["project_id", "requested_by_id", "status"],
        "media_hydration_items": ["hydration_job_id", "media_asset_id", "status"],
        "storage_retention_notices": ["user_id", "status"],
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    op.drop_table("storage_retention_notices")
    op.drop_table("media_hydration_items")
    op.drop_table("media_hydration_jobs")
    for column in ("raw_deleted_at", "restore_expires_at", "restore_requested_at", "archived_at", "archive_requested_at", "archive_status"):
        op.drop_column("media_assets", column)
    for column in ("last_accessed_at", "completed_at", "lifecycle_state"):
        op.drop_column("projects", column)
    op.drop_column("users", "last_login_at")
