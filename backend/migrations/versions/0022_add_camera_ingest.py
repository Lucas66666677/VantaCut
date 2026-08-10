"""add secure camera-to-cloud ingest sessions and chunks

Revision ID: 0022_add_camera_ingest
Revises: 0021_add_platform_api_billing
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0022_add_camera_ingest"
down_revision = "0021_add_platform_api_billing"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "camera_devices", *_timestamps(),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_identifier", sa.String(160), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("device_type", sa.String(80), nullable=False, server_default="camera"),
        sa.Column("encrypted_hmac_secret", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "device_identifier", name="uq_camera_device_project_identifier"),
    )
    op.create_table(
        "camera_ingest_sessions", *_timestamps(),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("capture_id", sa.String(160), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="capturing"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("total_duration_seconds", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("error_message", sa.Text()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["camera_devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["timeline_id"], ["timelines.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("device_id", "capture_id", name="uq_camera_ingest_device_capture"),
    )
    op.create_table(
        "camera_ingest_chunks", *_timestamps(),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(1000), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("mime_type", sa.String(120), nullable=False, server_default="video/mp4"),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Numeric(12, 3)),
        sa.Column("status", sa.String(32), nullable=False, server_default="received"),
        sa.Column("camera_metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("proxy_key", sa.String(1000)),
        sa.Column("media_asset_id", postgresql.UUID(as_uuid=True)),
        sa.Column("error_message", sa.Text()),
        sa.ForeignKeyConstraint(["session_id"], ["camera_ingest_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_asset_id"], ["media_assets.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("storage_key"),
        sa.UniqueConstraint("session_id", "sequence_number", name="uq_camera_ingest_session_sequence"),
    )
    for table, columns in {
        "camera_devices": ["project_id", "device_identifier", "is_active"],
        "camera_ingest_sessions": ["project_id", "device_id", "timeline_id", "capture_id", "status"],
        "camera_ingest_chunks": ["session_id", "content_sha256", "status", "media_asset_id"],
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    op.drop_table("camera_ingest_chunks")
    op.drop_table("camera_ingest_sessions")
    op.drop_table("camera_devices")
