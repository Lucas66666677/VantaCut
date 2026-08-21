"""restore AI-assistance table timestamp server defaults

Revision ID: 0039_fix_ai_assistance_timestamp_defaults
Revises: 0038_fix_voice_profiles_timestamp_defaults
"""
from alembic import op
import sqlalchemy as sa


revision = "0039_fix_ai_assistance_timestamp_defaults"
down_revision = "0038_fix_voice_profiles_timestamp_defaults"
branch_labels = None
depends_on = None

TABLES = ("agent_edit_runs", "avatar_profiles", "avatar_render_jobs")


def upgrade() -> None:
    for table in TABLES:
        for column in ("created_at", "updated_at"):
            op.alter_column(table, column, server_default=sa.func.now(), existing_type=sa.DateTime(timezone=True), existing_nullable=False)


def downgrade() -> None:
    for table in reversed(TABLES):
        for column in ("updated_at", "created_at"):
            op.alter_column(table, column, server_default=None, existing_type=sa.DateTime(timezone=True), existing_nullable=False)
