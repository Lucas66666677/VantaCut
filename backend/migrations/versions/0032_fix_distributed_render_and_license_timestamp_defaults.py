"""restore template_licenses/distributed_render_*/compute_credit_ledger timestamp server defaults

Revision ID: 0032_fix_distributed_render_and_license_timestamp_defaults
Revises: 0031_fix_marketplace_and_compute_timestamp_defaults
"""
from alembic import op
import sqlalchemy as sa


revision = "0032_fix_distributed_render_and_license_timestamp_defaults"
down_revision = "0031_fix_marketplace_and_compute_timestamp_defaults"
branch_labels = None
depends_on = None


# Closes out the same bug class fixed in 0030 (review_participants) and 0031
# (marketplace_templates/creator_connect_accounts/compute_nodes) for the five
# sibling tables flagged as out-of-scope-but-latent in 0031's docstring:
#
#   - template_licenses (created by 0024_add_template_marketplace.py's
#     `_timestamps()` helper — the same helper that caused the
#     marketplace_templates/creator_connect_accounts bug).
#   - distributed_render_batches, distributed_render_chunks,
#     distributed_render_assignments, compute_credit_ledger (created by
#     0028_add_distributed_compute.py's `_base_columns()` helper — the same
#     helper that caused the compute_nodes bug).
#
# This mismatch was proven for all five, not assumed, by reading both sides
# directly rather than pattern-matching on table name:
#
#   1. ORM: all five model classes (app/models/entities.py) inherit
#      TimestampMixin (app/models/base.py), which declares
#      `server_default=func.now()` for both created_at and updated_at.
#   2. DB DDL: 0024's `_timestamps()` and 0028's `_base_columns()` both
#      create created_at/updated_at as `nullable=False` with NO
#      `server_default` argument — unlike other columns created in the same
#      `op.create_table()` calls (e.g. `status`, `price_cents`, `node_kind`),
#      which correctly do pass `server_default=...`. No later migration
#      touches these five tables' timestamp columns either (confirmed by
#      searching every migration file).
#   3. Production insertion path: every one of the five tables already has
#      a real, existing call site that constructs the model without
#      explicit timestamps — app/api/v1/marketplace.py's `create_checkout`
#      (`TemplateLicense(...)`) and app/services/distributed_compute.py's
#      `create_batch`/`assign_next_chunk`/`verify_chunk_result`
#      (`DistributedRenderBatch`, `DistributedRenderChunk`,
#      `DistributedRenderAssignment`, `ComputeCreditLedger`) all omit
#      created_at/updated_at, trusting the (previously nonexistent)
#      database default. This is not a hypothetical bug: any of these five
#      code paths would hit a live NotNullViolation against a real Postgres
#      database today, exactly like review_participants and
#      marketplace_templates did before their fixes.
#
# Same fix, same safety properties as 0030/0031: additive only. ALTER
# COLUMN ... SET DEFAULT has no effect on rows already in the table — it
# only changes what a future INSERT falls back to when the column is
# omitted. No existing data is touched, no other column or table is
# touched, and 0024/0028 are not edited.
_TABLES = (
    "template_licenses",
    "distributed_render_batches",
    "distributed_render_chunks",
    "distributed_render_assignments",
    "compute_credit_ledger",
)


def upgrade() -> None:
    for table in _TABLES:
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
    for table in reversed(_TABLES):
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
