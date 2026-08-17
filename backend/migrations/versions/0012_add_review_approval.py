"""add frame accurate review comments and approvals

Revision ID: 0012_add_review_approval
Revises: 0011_add_agent_edit_versions
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0012_add_review_approval"
down_revision = "0011_add_agent_edit_versions"
branch_labels = None
depends_on = None


review_status = sa.Enum(
    "draft", "in_review", "approved", "changes_requested", name="review_status", create_type=False
)
comment_status = sa.Enum("open", "resolved", name="comment_status", create_type=False)
review_role = sa.Enum("reviewer", "approver", name="review_role", create_type=False)


def upgrade() -> None:
    review_status.create(op.get_bind(), checkfirst=True)
    comment_status.create(op.get_bind(), checkfirst=True)
    review_role.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "timeline_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("status", review_status, nullable=False, server_default="draft"),
        sa.Column("requested_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["timeline_id"], ["timelines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decided_by_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_timeline_reviews_timeline_id", "timeline_reviews", ["timeline_id"])
    op.create_index("ix_timeline_reviews_status", "timeline_reviews", ["status"])
    op.create_table(
        "review_participants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", review_role, nullable=False, server_default="reviewer"),
        sa.ForeignKeyConstraint(["timeline_id"], ["timelines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("timeline_id", "user_id"),
    )
    op.create_index("ix_review_participants_timeline_id", "review_participants", ["timeline_id"])
    op.create_index("ix_review_participants_user_id", "review_participants", ["user_id"])
    op.create_table(
        "review_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("frame_number", sa.Integer(), nullable=False),
        sa.Column("frame_rate", sa.Numeric(10, 4), nullable=False),
        sa.Column("time_seconds", sa.Numeric(12, 4), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("annotation_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("status", comment_status, nullable=False, server_default="open"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["timeline_id"], ["timelines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="CASCADE"),
    )
    for name, column in (("project_id", "project_id"), ("timeline_id", "timeline_id"), ("author_id", "author_id"), ("frame_number", "frame_number"), ("status", "status")):
        op.create_index(f"ix_review_comments_{name}", "review_comments", [column])


def downgrade() -> None:
    op.drop_table("review_comments")
    op.drop_table("review_participants")
    op.drop_table("timeline_reviews")
    comment_status.drop(op.get_bind(), checkfirst=True)
    review_status.drop(op.get_bind(), checkfirst=True)
    review_role.drop(op.get_bind(), checkfirst=True)
