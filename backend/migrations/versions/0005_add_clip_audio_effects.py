"""add clip audio effects

Revision ID: 0005_add_clip_audio_effects
Revises: 0004_add_multitrack_clips
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0005_add_clip_audio_effects"
down_revision = "0004_add_multitrack_clips"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clips",
        sa.Column(
            "audio_effects",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("clips", "audio_effects")
