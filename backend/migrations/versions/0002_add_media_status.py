"""add media upload status

Revision ID: 0002_add_media_status
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa


revision = "0002_add_media_status"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    media_status = sa.Enum(
        "UPLOADING", "PROCESSING", "READY", "FAILED", name="media_status",
        create_type=False,
    )
    media_status.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "media_assets",
        sa.Column(
            "status",
            media_status,
            nullable=False,
            server_default="UPLOADING",
        ),
    )
    op.create_index("ix_media_assets_status", "media_assets", ["status"])


def downgrade() -> None:
    op.drop_index("ix_media_assets_status", table_name="media_assets")
    op.drop_column("media_assets", "status")
    sa.Enum(
        "UPLOADING", "PROCESSING", "READY", "FAILED", name="media_status"
    ).drop(op.get_bind(), checkfirst=True)

