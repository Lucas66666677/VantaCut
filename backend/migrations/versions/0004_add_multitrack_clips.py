"""add multitrack clip attributes

Revision ID: 0004_add_multitrack_clips
Revises: 0003_add_derived_media_fields
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0004_add_multitrack_clips"
down_revision = "0003_add_derived_media_fields"
branch_labels = None
depends_on = None


track_type = postgresql.ENUM("MAIN_VIDEO", "B_ROLL", "AUDIO_OVERLAY", name="track_type", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    track_type.create(bind, checkfirst=True)
    # Legacy single-track values become main_video before switching to the enum.
    op.execute("UPDATE clips SET track = 'MAIN_VIDEO' WHERE track IN ('video', 'main', 'AI 粗剪建議')")
    op.alter_column(
        "clips",
        "track",
        existing_type=sa.String(length=50),
        type_=track_type,
        postgresql_using="upper(track)::track_type",
        nullable=False,
    )
    op.add_column("clips", sa.Column("z_index", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("clips", sa.Column("audio_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_index("ix_clips_track", "clips", ["track"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_clips_track", table_name="clips")
    op.drop_column("clips", "audio_enabled")
    op.drop_column("clips", "z_index")
    op.alter_column(
        "clips",
        "track",
        existing_type=track_type,
        type_=sa.String(length=50),
        postgresql_using="lower(track::text)",
        nullable=False,
    )
    track_type.drop(op.get_bind(), checkfirst=True)
