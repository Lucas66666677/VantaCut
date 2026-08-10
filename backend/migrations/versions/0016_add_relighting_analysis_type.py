"""add virtual relighting analysis type

Revision ID: 0016_add_relighting_analysis_type
Revises: 0015_add_screen_focus_analysis_type
"""
from alembic import op


revision = "0016_add_relighting_analysis_type"
down_revision = "0015_add_screen_focus_analysis_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE analysis_type ADD VALUE IF NOT EXISTS 'RELIGHTING'")


def downgrade() -> None:
    pass
