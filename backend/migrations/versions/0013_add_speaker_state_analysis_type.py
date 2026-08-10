"""add speaker-state analysis type

Revision ID: 0013_add_speaker_state_analysis_type
Revises: 0012_add_review_approval
"""
from alembic import op


revision = "0013_add_speaker_state_analysis_type"
down_revision = "0012_add_review_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing migrations create this enum from SQLAlchemy member names.
    op.execute("ALTER TYPE analysis_type ADD VALUE IF NOT EXISTS 'SPEAKER_STATE'")


def downgrade() -> None:
    # PostgreSQL cannot safely remove an enum value in-place.
    pass
