"""add media metadata and derived object keys

Revision ID: 0003_add_derived_media_fields
Revises: 0002_add_media_status
"""
from alembic import op
import sqlalchemy as sa


revision = "0003_add_derived_media_fields"
down_revision = "0002_add_media_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("media_assets", sa.Column("thumbnail_key", sa.String(1000)))
    op.add_column("media_assets", sa.Column("audio_key", sa.String(1000)))
    op.add_column("media_assets", sa.Column("proxy_key", sa.String(1000)))
    op.add_column("media_assets", sa.Column("fps", sa.Numeric(8, 3)))
    op.add_column("media_assets", sa.Column("video_codec", sa.String(50)))


def downgrade() -> None:
    op.drop_column("media_assets", "video_codec")
    op.drop_column("media_assets", "fps")
    op.drop_column("media_assets", "proxy_key")
    op.drop_column("media_assets", "audio_key")
    op.drop_column("media_assets", "thumbnail_key")

