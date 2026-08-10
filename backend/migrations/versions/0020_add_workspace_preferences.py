"""persist adaptive editor workspace layouts

Revision ID: 0020_add_workspace_preferences
Revises: 0019_add_render_forensic_provenance
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0020_add_workspace_preferences"
down_revision = "0019_add_render_forensic_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("layout_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("layout_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "project_id", name="uq_workspace_preferences_user_project"),
    )
    op.create_index("ix_workspace_preferences_user_id", "workspace_preferences", ["user_id"])
    op.create_index("ix_workspace_preferences_project_id", "workspace_preferences", ["project_id"])


def downgrade() -> None:
    op.drop_table("workspace_preferences")
