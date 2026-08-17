"""add social OAuth accounts, publishing records and thumbnail experiments

Revision ID: 0014_add_social_publishing
Revises: 0013_add_speaker_state_analysis_type
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0014_add_social_publishing"
down_revision = "0013_add_speaker_state_analysis_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # postgresql.ENUM (not sa.Enum) with create_type=False: a plain
    # sa.Enum's create_type flag is dropped when SQLAlchemy adapts it to
    # the native PG ENUM for column-level DDL (adapt_emulated_to_native()
    # only carries create_type over when the source type is already
    # NativeForEmulated), so the op.create_table() calls below would
    # silently re-CREATE TYPE and collide with the explicit .create() calls.
    # See migrations/env.py and 0001_initial.py / 0028_add_distributed_compute.py
    # for the same fix.
    social_platform = postgresql.ENUM("youtube", "tiktok", name="social_platform", create_type=False)
    post_status = postgresql.ENUM(
        "queued", "publishing", "published", "awaiting_creator", "failed",
        name="social_post_status", create_type=False,
    )
    experiment_status = postgresql.ENUM(
        "active", "completed", "insufficient_data", "failed",
        name="thumbnail_experiment_status", create_type=False,
    )
    bind = op.get_bind()
    social_platform.create(bind, checkfirst=True)
    post_status.create(bind, checkfirst=True)
    experiment_status.create(bind, checkfirst=True)

    op.create_table(
        "social_accounts",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", social_platform, nullable=False),
        sa.Column("platform_account_id", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255)),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text()),
        sa.Column("token_expires_at", sa.DateTime(timezone=True)),
        sa.Column("scopes_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("profile_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "platform", "platform_account_id"),
    )
    op.create_index("ix_social_accounts_user_id", "social_accounts", ["user_id"])
    op.create_index("ix_social_accounts_platform", "social_accounts", ["platform"])
    op.create_index("ix_social_accounts_token_expires_at", "social_accounts", ["token_expires_at"])

    op.create_table(
        "social_posts",
        sa.Column("social_account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("timelines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("render_job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("render_jobs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("platform_post_id", sa.String(length=255)),
        sa.Column("status", post_status, nullable=False, server_default="queued"),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("social_account_id", "timeline_id", "render_job_id", "platform_post_id", "status", "published_at"):
        op.create_index(f"ix_social_posts_{column}", "social_posts", [column])

    op.create_table(
        "thumbnail_experiments",
        sa.Column("social_post_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("social_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", experiment_status, nullable=False, server_default="active"),
        sa.Column("candidates_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("active_candidate_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("winner_candidate_id", sa.String(length=120)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("social_post_id"),
    )
    for column in ("social_post_id", "status", "started_at", "ends_at"):
        op.create_index(f"ix_thumbnail_experiments_{column}", "thumbnail_experiments", [column])

    op.create_table(
        "thumbnail_observations",
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("thumbnail_experiments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_id", sa.String(length=120), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("impressions", sa.Integer()),
        sa.Column("click_through_rate", sa.Numeric(precision=8, scale=6)),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("experiment_id", "candidate_id", "observed_at"):
        op.create_index(f"ix_thumbnail_observations_{column}", "thumbnail_observations", [column])


def downgrade() -> None:
    op.drop_table("thumbnail_observations")
    op.drop_table("thumbnail_experiments")
    op.drop_table("social_posts")
    op.drop_table("social_accounts")
    bind = op.get_bind()
    sa.Enum(name="thumbnail_experiment_status").drop(bind, checkfirst=True)
    sa.Enum(name="social_post_status").drop(bind, checkfirst=True)
    sa.Enum(name="social_platform").drop(bind, checkfirst=True)
