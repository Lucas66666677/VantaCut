"""add interactive branching playback telemetry

Revision ID: 0026_add_interactive_branching
Revises: 0025_add_media_asset_lifecycle
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0026_add_interactive_branching"
down_revision = "0025_add_media_asset_lifecycle"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "interactive_playback_sessions", *_timestamps(),
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("viewer_token_hash", sa.String(64)), sa.Column("current_node_id", sa.String(120)),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False), sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("total_watch_seconds", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["timeline_id"], ["timelines.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "interactive_playback_events", *_timestamps(),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("node_id", sa.String(120), nullable=False), sa.Column("edge_id", sa.String(120)), sa.Column("target_node_id", sa.String(120)),
        sa.Column("watch_seconds", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("event_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["session_id"], ["interactive_playback_sessions.id"], ondelete="CASCADE"),
    )
    for table, columns in {
        "interactive_playback_sessions": ["timeline_id", "viewer_token_hash", "current_node_id", "status"],
        "interactive_playback_events": ["session_id", "event_type", "node_id", "edge_id", "target_node_id"],
    }.items():
        for column in columns: op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    op.drop_table("interactive_playback_events")
    op.drop_table("interactive_playback_sessions")
