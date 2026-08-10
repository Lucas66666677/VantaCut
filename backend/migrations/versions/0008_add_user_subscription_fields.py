"""add user subscription tiers and render credits

Revision ID: 0008_add_user_subscription_fields
Revises: 0007_add_media_embeddings
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0008_add_user_subscription_fields"
down_revision = "0007_add_media_embeddings"
branch_labels = None
depends_on = None


subscription_tier = postgresql.ENUM("free", "pro", name="subscription_tier")


def upgrade() -> None:
    bind = op.get_bind()
    subscription_tier.create(bind, checkfirst=True)
    op.add_column(
        "users",
        sa.Column("subscription_tier", subscription_tier, nullable=False, server_default="free"),
    )
    op.add_column(
        "users",
        sa.Column("render_credits", sa.Integer(), nullable=False, server_default="10"),
    )


def downgrade() -> None:
    op.drop_column("users", "render_credits")
    op.drop_column("users", "subscription_tier")
    subscription_tier.drop(op.get_bind(), checkfirst=True)
