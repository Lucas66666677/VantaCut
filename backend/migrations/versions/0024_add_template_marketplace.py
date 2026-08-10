"""add encrypted template marketplace, Stripe Connect accounts and immutable ledger

Revision ID: 0024_add_template_marketplace
Revises: 0023_add_spatial_video_jobs
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0024_add_template_marketplace"
down_revision = "0023_add_spatial_video_jobs"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "marketplace_templates", *_timestamps(),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("creator_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(180), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("price_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="usd"),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("encryption_key_version", sa.String(32), nullable=False, server_default="v1"),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("safe_preview_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("template_id"), sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "creator_connect_accounts", *_timestamps(),
        sa.Column("creator_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stripe_account_id", sa.String(255), nullable=False),
        sa.Column("details_submitted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("charges_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payouts_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("creator_id"), sa.UniqueConstraint("stripe_account_id"),
    )
    op.create_table(
        "template_licenses", *_timestamps(),
        sa.Column("marketplace_template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("buyer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True)),
        sa.Column("render_job_id", postgresql.UUID(as_uuid=True)),
        sa.Column("status", sa.String(32), nullable=False, server_default="checkout_pending"),
        sa.Column("gross_amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="usd"),
        sa.Column("creator_share_cents", sa.Integer(), nullable=False),
        sa.Column("platform_share_cents", sa.Integer(), nullable=False),
        sa.Column("stripe_payment_intent_id", sa.String(255)),
        sa.Column("stripe_charge_id", sa.String(255)),
        sa.Column("stripe_transfer_id", sa.String(255)),
        sa.Column("transfer_group", sa.String(255), nullable=False),
        sa.Column("template_payload_sha256", sa.String(64), nullable=False),
        sa.Column("blackbox_render_only", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["marketplace_template_id"], ["marketplace_templates.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["buyer_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["timeline_id"], ["timelines.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["render_job_id"], ["render_jobs.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("render_job_id"), sa.UniqueConstraint("stripe_payment_intent_id"),
        sa.UniqueConstraint("stripe_charge_id"), sa.UniqueConstraint("stripe_transfer_id"),
        sa.UniqueConstraint("transfer_group"),
    )
    op.create_table(
        "marketplace_ledger_entries", *_timestamps(),
        sa.Column("license_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_type", sa.String(48), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("stripe_object_id", sa.String(255)),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["license_id"], ["template_licenses.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for table, columns in {
        "marketplace_templates": ["template_id", "creator_id", "slug", "status", "payload_sha256"],
        "creator_connect_accounts": ["creator_id", "stripe_account_id"],
        "template_licenses": ["marketplace_template_id", "buyer_id", "project_id", "timeline_id", "render_job_id", "status", "stripe_payment_intent_id", "stripe_charge_id", "stripe_transfer_id", "transfer_group", "applied_at", "fulfilled_at"],
        "marketplace_ledger_entries": ["license_id", "entry_type", "status", "stripe_object_id"],
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    op.drop_table("marketplace_ledger_entries")
    op.drop_table("template_licenses")
    op.drop_table("creator_connect_accounts")
    op.drop_table("marketplace_templates")
