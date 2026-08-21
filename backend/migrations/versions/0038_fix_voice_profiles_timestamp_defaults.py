"""restore voice_profiles timestamp server defaults

Revision ID: 0038_fix_voice_profiles_timestamp_defaults
Revises: 0037_fix_workspace_preferences_timestamp_defaults
"""
from alembic import op
import sqlalchemy as sa


revision = "0038_fix_voice_profiles_timestamp_defaults"
down_revision = "0037_fix_workspace_preferences_timestamp_defaults"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("created_at", "updated_at"):
        op.alter_column("voice_profiles", column, server_default=sa.func.now(), existing_type=sa.DateTime(timezone=True), existing_nullable=False)


def downgrade() -> None:
    for column in ("updated_at", "created_at"):
        op.alter_column("voice_profiles", column, server_default=None, existing_type=sa.DateTime(timezone=True), existing_nullable=False)
