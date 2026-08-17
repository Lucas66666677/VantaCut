"""add user auth fields (hashed_password, is_active)

Revision ID: 0029_add_user_auth_fields
Revises: 0028_add_distributed_compute
"""
from alembic import op
import sqlalchemy as sa


revision = "0029_add_user_auth_fields"
down_revision = "0028_add_distributed_compute"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("hashed_password", sa.String(length=255), nullable=True))
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("users", "is_active")
    op.drop_column("users", "hashed_password")
