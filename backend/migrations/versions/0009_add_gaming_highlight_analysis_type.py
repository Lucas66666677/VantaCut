"""add gaming highlights analysis type

Revision ID: 0009_add_gaming_highlight_analysis_type
Revises: 0008_add_user_subscription_fields
"""
from alembic import op


revision = "0009_add_gaming_highlight_analysis_type"
down_revision = "0008_add_user_subscription_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE analysis_type ADD VALUE IF NOT EXISTS 'GAMING_HIGHLIGHTS'")


def downgrade() -> None:
    # PostgreSQL cannot safely remove an enum value in-place.
    pass
