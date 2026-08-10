"""add AI feedback events

Revision ID: 0006_add_ai_feedback
Revises: 0005_add_clip_audio_effects
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0006_add_ai_feedback"
down_revision = "0005_add_clip_audio_effects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("clip_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("original_ai_decision", sa.String(length=16), nullable=False),
        sa.Column("user_final_decision", sa.String(length=16), nullable=False),
        sa.Column("clip_context_features", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["timeline_id"], ["timelines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["clip_id"], ["clips.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_ai_feedback_user_id", "ai_feedback", ["user_id"])
    op.create_index("ix_ai_feedback_project_id", "ai_feedback", ["project_id"])
    op.create_index("ix_ai_feedback_timeline_id", "ai_feedback", ["timeline_id"])
    op.create_index("ix_ai_feedback_clip_id", "ai_feedback", ["clip_id"])


def downgrade() -> None:
    op.drop_table("ai_feedback")
