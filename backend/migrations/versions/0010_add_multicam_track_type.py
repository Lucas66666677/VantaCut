"""add multicam video track type

Revision ID: 0010_add_multicam_track_type
Revises: 0009_add_gaming_highlight_analysis_type
"""
from alembic import op


revision = "0010_add_multicam_track_type"
down_revision = "0009_add_gaming_highlight_analysis_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE track_type ADD VALUE IF NOT EXISTS 'MULTICAM_VIDEO'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be dropped in-place.
    pass
