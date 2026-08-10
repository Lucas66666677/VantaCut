"""add virtual avatar profiles and alpha render jobs

Revision ID: 0027_add_virtual_avatar
Revises: 0026_add_interactive_branching
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0027_add_virtual_avatar"
down_revision = "0026_add_interactive_branching"
branch_labels = None
depends_on = None

def _timestamps() -> list[sa.Column]:
    return [sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]

def upgrade() -> None:
    op.create_table("avatar_profiles", *_timestamps(), sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("project_id", postgresql.UUID(as_uuid=True)), sa.Column("name", sa.String(160), nullable=False), sa.Column("renderer", sa.String(32), nullable=False, server_default="unreal_mrq"), sa.Column("asset_bundle_key", sa.String(1000), nullable=False), sa.Column("rig_mapping_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"), sa.Column("consent_recorded_at", sa.DateTime(timezone=True), nullable=False), sa.Column("status", sa.String(32), nullable=False, server_default="ready"), sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"))
    op.create_table("avatar_render_jobs", *_timestamps(), sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("timeline_id", postgresql.UUID(as_uuid=True)), sa.Column("avatar_profile_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("source_asset_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("source_start", sa.Numeric(12, 3), nullable=False), sa.Column("source_end", sa.Numeric(12, 3), nullable=False), sa.Column("status", sa.String(32), nullable=False, server_default="queued"), sa.Column("progress", sa.Integer(), nullable=False, server_default="0"), sa.Column("blendshape_key", sa.String(1000)), sa.Column("motion_key", sa.String(1000)), sa.Column("rgba_video_key", sa.String(1000)), sa.Column("output_asset_id", postgresql.UUID(as_uuid=True)), sa.Column("error_message", sa.Text()), sa.Column("provenance_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"), sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["timeline_id"], ["timelines.id"], ondelete="SET NULL"), sa.ForeignKeyConstraint(["avatar_profile_id"], ["avatar_profiles.id"], ondelete="RESTRICT"), sa.ForeignKeyConstraint(["source_asset_id"], ["media_assets.id"], ondelete="RESTRICT"), sa.ForeignKeyConstraint(["output_asset_id"], ["media_assets.id"], ondelete="SET NULL"))
    for table, columns in {"avatar_profiles": ["owner_id", "project_id", "status"], "avatar_render_jobs": ["project_id", "timeline_id", "avatar_profile_id", "source_asset_id", "status", "output_asset_id"]}.items():
        for column in columns: op.create_index(f"ix_{table}_{column}", table, [column])

def downgrade() -> None:
    op.drop_table("avatar_render_jobs"); op.drop_table("avatar_profiles")
