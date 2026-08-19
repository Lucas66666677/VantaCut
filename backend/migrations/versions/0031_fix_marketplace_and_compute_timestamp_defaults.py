"""restore marketplace_templates/creator_connect_accounts/compute_nodes timestamp server defaults

Revision ID: 0031_fix_marketplace_and_compute_timestamp_defaults
Revises: 0030_fix_review_participants_timestamp_defaults
"""
from alembic import op
import sqlalchemy as sa


revision = "0031_fix_marketplace_and_compute_timestamp_defaults"
down_revision = "0030_fix_review_participants_timestamp_defaults"
branch_labels = None
depends_on = None


# Same bug class, same fix, as 0030_fix_review_participants_timestamp_defaults
# (see that migration's docstring for the full background). Two separate
# migrations reused a local `created_at`/`updated_at` column-builder helper
# that declared `nullable=False` with no `server_default`, even though the
# corresponding ORM models (via TimestampMixin, app/models/base.py) declare
# `server_default=func.now()` for both columns on every affected table:
#
#   - 0024_add_template_marketplace.py's `_timestamps()` helper — affects
#     marketplace_templates, creator_connect_accounts, template_licenses.
#   - 0028_add_distributed_compute.py's `_base_columns()` helper — affects
#     compute_nodes, distributed_render_batches, distributed_render_chunks,
#     distributed_render_assignments, compute_credit_ledger.
#
# This migration fixes exactly the three tables Batch 2B's own code and
# tests actually exercise, evidenced by a real CI failure against a live
# Postgres service container:
#
#   - marketplace_templates: backend/tests/test_marketplace_dashboard.py's
#     `_make_marketplace_template` constructs `MarketplaceTemplate(...)`
#     without explicit timestamps (matching how a real listing would be
#     created), and hit `psycopg.errors.NotNullViolation: null value in
#     column "created_at" of relation "marketplace_templates"`.
#   - creator_connect_accounts: the same test file's
#     `test_owner_sees_own_dashboard` constructs a `CreatorConnectAccount`
#     the same way and hit the identical violation.
#   - compute_nodes: app/api/v1/distributed_compute.py's
#     `enroll_compute_node` (this batch's own fixed route) constructs
#     `ComputeNode(owner_id=current_user.id, ...)` without explicit
#     timestamps — the identical insert pattern, confirmed by direct
#     reading of 0028_add_distributed_compute.py's `_base_columns()` to
#     share the exact same missing-server_default gap. Left unfixed, this
#     would have failed the same way the moment
#     backend/tests/test_distributed_compute.py's enrollment tests ran
#     (that CI step never got a chance to run in this batch's first CI
#     attempt, since the marketplace_templates failure aborted the job
#     first).
#
# template_licenses and the other four distributed-compute tables
# (distributed_render_batches, distributed_render_chunks,
# distributed_render_assignments, compute_credit_ledger) share the
# identical latent bug but are not written to by any Batch 2B code path or
# test, so fixing them here would be an unevidenced, out-of-scope change.
# They're flagged in this batch's PR/report as a recommended follow-up
# mechanical fix, not silently left undocumented.
#
# Exactly like 0030: this only adds the missing DEFAULT clause to existing
# columns. ALTER COLUMN ... SET DEFAULT has no effect on rows already in
# the table — it only changes what a future INSERT falls back to when the
# column is omitted. No existing data is touched, no other column or table
# is touched, and 0024/0028 are not edited. Fully additive, non-destructive.
def upgrade() -> None:
    for table in ("marketplace_templates", "creator_connect_accounts", "compute_nodes"):
        op.alter_column(
            table, "created_at",
            server_default=sa.func.now(),
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )
        op.alter_column(
            table, "updated_at",
            server_default=sa.func.now(),
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )


def downgrade() -> None:
    for table in ("compute_nodes", "creator_connect_accounts", "marketplace_templates"):
        op.alter_column(
            table, "updated_at",
            server_default=None,
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )
        op.alter_column(
            table, "created_at",
            server_default=None,
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )
