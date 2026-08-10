"""create initial video editor schema

Revision ID: 0001_initial
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


media_type = postgresql.ENUM("VIDEO", "AUDIO", "IMAGE", name="media_type")
analysis_type = postgresql.ENUM(
    "TEMPLATE", "ROUGH_CUT", "TRANSCRIPTION", "MOOD", name="analysis_type"
)
render_status = postgresql.ENUM(
    "QUEUED", "PROCESSING", "COMPLETED", "FAILED", name="render_status"
)


def upgrade() -> None:
    bind = op.get_bind()
    media_type.create(bind, checkfirst=True)
    analysis_type.create(bind, checkfirst=True)
    render_status.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=False)

    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])

    op.create_table(
        "media_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("storage_key", sa.String(1000), nullable=False),
        sa.Column("media_type", media_type, nullable=False),
        sa.Column("mime_type", sa.String(120)),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("duration_seconds", sa.Numeric(12, 3)),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_media_assets_project_id", "media_assets", ["project_id"])

    op.create_table(
        "templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_asset_id", postgresql.UUID(as_uuid=True)),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("structure_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_asset_id"], ["media_assets.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_templates_project_id", "templates", ["project_id"])

    op.create_table(
        "timelines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("settings_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_timelines_project_id", "timelines", ["project_id"])

    op.create_table(
        "clips",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_start", sa.Numeric(12, 3), nullable=False),
        sa.Column("source_end", sa.Numeric(12, 3), nullable=False),
        sa.Column("track", sa.String(50), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["timeline_id"], ["timelines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_asset_id"], ["media_assets.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("timeline_id", "order_index"),
    )
    op.create_index("ix_clips_timeline_id", "clips", ["timeline_id"])
    op.create_index("ix_clips_source_asset_id", "clips", ["source_asset_id"])

    op.create_table(
        "ai_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("media_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_type", analysis_type, nullable=False),
        sa.Column("model_name", sa.String(160)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("result_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["media_asset_id"], ["media_assets.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_ai_analyses_media_asset_id", "ai_analyses", ["media_asset_id"])

    op.create_table(
        "render_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", render_status, nullable=False),
        sa.Column("output_key", sa.String(1000)),
        sa.Column("output_format", sa.String(20), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["timeline_id"], ["timelines.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_render_jobs_project_id", "render_jobs", ["project_id"])
    op.create_index("ix_render_jobs_timeline_id", "render_jobs", ["timeline_id"])


def downgrade() -> None:
    for table in ("render_jobs", "ai_analyses", "clips", "timelines", "templates", "media_assets", "projects", "users"):
        op.drop_table(table)
    bind = op.get_bind()
    render_status.drop(bind, checkfirst=True)
    analysis_type.drop(bind, checkfirst=True)
    media_type.drop(bind, checkfirst=True)

