"""add headless platform keys, jobs, usage events and invoices

Revision ID: 0021_add_platform_api_billing
Revises: 0020_add_workspace_preferences
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0021_add_platform_api_billing"
down_revision = "0020_add_workspace_preferences"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade() -> None:
    op.create_table("platform_api_keys", *_timestamps(), sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("name", sa.String(160), nullable=False), sa.Column("key_prefix", sa.String(24), nullable=False), sa.Column("key_hash", sa.String(128), nullable=False), sa.Column("webhook_url", sa.String(2048)), sa.Column("encrypted_webhook_secret", sa.Text()), sa.Column("rate_limit_rps", sa.Numeric(8, 3), nullable=False, server_default="2"), sa.Column("burst_limit", sa.Integer(), nullable=False, server_default="10"), sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("last_used_at", sa.DateTime(timezone=True)), sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"), sa.UniqueConstraint("key_prefix"), sa.UniqueConstraint("key_hash"))
    op.create_table("platform_jobs", *_timestamps(), sa.Column("api_key_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("idempotency_key", sa.String(255), nullable=False), sa.Column("operation", sa.String(64), nullable=False), sa.Column("status", sa.String(32), nullable=False, server_default="queued"), sa.Column("source_url", sa.String(2048), nullable=False), sa.Column("request_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"), sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"), sa.Column("error_message", sa.Text()), sa.Column("webhook_url", sa.String(2048)), sa.Column("webhook_attempts", sa.Integer(), nullable=False, server_default="0"), sa.Column("last_webhook_status", sa.Integer()), sa.ForeignKeyConstraint(["api_key_id"], ["platform_api_keys.id"], ondelete="CASCADE"), sa.UniqueConstraint("api_key_id", "idempotency_key", name="uq_platform_jobs_key_idempotency"))
    op.create_table("platform_usage_events", *_timestamps(), sa.Column("api_key_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("platform_job_id", postgresql.UUID(as_uuid=True)), sa.Column("metric", sa.String(64), nullable=False), sa.Column("quantity", sa.Numeric(16, 4), nullable=False), sa.Column("dimensions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"), sa.ForeignKeyConstraint(["api_key_id"], ["platform_api_keys.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["platform_job_id"], ["platform_jobs.id"], ondelete="SET NULL"))
    op.create_table("platform_invoices", *_timestamps(), sa.Column("api_key_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("period_start", sa.DateTime(timezone=True), nullable=False), sa.Column("period_end", sa.DateTime(timezone=True), nullable=False), sa.Column("status", sa.String(32), nullable=False, server_default="draft"), sa.Column("totals_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"), sa.ForeignKeyConstraint(["api_key_id"], ["platform_api_keys.id"], ondelete="CASCADE"), sa.UniqueConstraint("api_key_id", "period_start", name="uq_platform_invoices_key_period"))
    for table, columns in {"platform_api_keys": ["owner_id", "key_prefix", "key_hash", "is_active"], "platform_jobs": ["api_key_id", "operation", "status"], "platform_usage_events": ["api_key_id", "platform_job_id", "metric"], "platform_invoices": ["api_key_id", "period_start", "status"]}.items():
        for column in columns: op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    op.drop_table("platform_invoices"); op.drop_table("platform_usage_events"); op.drop_table("platform_jobs"); op.drop_table("platform_api_keys")
