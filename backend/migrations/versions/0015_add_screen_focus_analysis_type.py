"""add screen-focus analysis type

Revision ID: 0015_add_screen_focus_analysis_type
Revises: 0014_add_social_publishing
"""
from alembic import op


revision = "0015_add_screen_focus_analysis_type"
down_revision = "0014_add_social_publishing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The original SQLAlchemy enum persists member names (upper case).
    op.execute("ALTER TYPE analysis_type ADD VALUE IF NOT EXISTS 'SCREEN_FOCUS'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely in place.
    pass
