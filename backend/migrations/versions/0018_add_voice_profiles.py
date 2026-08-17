"""add consent-bound voice profiles

Revision ID: 0018_add_voice_profiles
Revises: 0017_add_auto_director_runs
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0018_add_voice_profiles"
down_revision = "0017_add_auto_director_runs"
branch_labels = None
depends_on = None

# postgresql.ENUM (not sa.Enum) with create_type=False: a plain sa.Enum's
# create_type flag is dropped when SQLAlchemy adapts it to the native PG
# ENUM for column-level DDL (adapt_emulated_to_native() only carries
# create_type over when the source type is already NativeForEmulated), so
# op.create_table() below would silently re-CREATE TYPE and collide with
# the explicit .create() call. See migrations/env.py and 0001_initial.py /
# 0028_add_distributed_compute.py for the same fix.
voice_profile_status = postgresql.ENUM(
    "queued", "extracting", "ready", "failed", name="voice_profile_status", create_type=False
)


def upgrade() -> None:
    voice_profile_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "voice_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_media_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("provider_name", sa.String(length=120), nullable=False),
        sa.Column("status", voice_profile_status, nullable=False, server_default="queued"),
        sa.Column("reference_audio_key", sa.String(length=1000)),
        sa.Column("conditioning_artifact_key", sa.String(length=1000)),
        sa.Column("language", sa.String(length=20)),
        sa.Column("reference_start_seconds", sa.Numeric(12, 3)),
        sa.Column("reference_duration_seconds", sa.Numeric(12, 3)),
        sa.Column("quality_score", sa.Numeric(5, 4)),
        sa.Column("consent_recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("error_message", sa.Text()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_media_asset_id"], ["media_assets.id"], ondelete="RESTRICT"),
    )
    for column in ("project_id", "created_by_id", "source_media_asset_id", "status"):
        op.create_index(f"ix_voice_profiles_{column}", "voice_profiles", [column])


def downgrade() -> None:
    op.drop_table("voice_profiles")
    voice_profile_status.drop(op.get_bind(), checkfirst=True)
