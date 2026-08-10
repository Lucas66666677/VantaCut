"""persist forensic watermark and C2PA delivery metadata

Revision ID: 0019_add_render_forensic_provenance
Revises: 0018_add_voice_profiles
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0019_add_render_forensic_provenance"
down_revision = "0018_add_voice_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("render_jobs", sa.Column("provenance_key", sa.String(length=1000), nullable=True))
    op.add_column(
        "render_jobs",
        sa.Column("forensic_metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("render_jobs", "forensic_metadata_json")
    op.drop_column("render_jobs", "provenance_key")
