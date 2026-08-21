"""restore review and social table timestamp server defaults

Revision ID: 0040_fix_review_social_timestamp_defaults
Revises: 0039_fix_ai_assistance_timestamp_defaults
"""
from alembic import op
import sqlalchemy as sa


revision = "0040_fix_review_social_timestamp_defaults"
down_revision = "0039_fix_ai_assistance_timestamp_defaults"
branch_labels = None
depends_on = None

TABLES = ("timeline_reviews", "review_comments", "social_accounts", "social_posts")


def upgrade() -> None:
    for table in TABLES:
        for column in ("created_at", "updated_at"):
            op.alter_column(table, column, server_default=sa.func.now(), existing_type=sa.DateTime(timezone=True), existing_nullable=False)


def downgrade() -> None:
    for table in reversed(TABLES):
        for column in ("updated_at", "created_at"):
            op.alter_column(table, column, server_default=None, existing_type=sa.DateTime(timezone=True), existing_nullable=False)
