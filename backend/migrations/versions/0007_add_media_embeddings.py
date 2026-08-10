"""add pgvector semantic media embeddings

Revision ID: 0007_add_media_embeddings
Revises: 0006_add_ai_feedback
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


revision = "0007_add_media_embeddings"
down_revision = "0006_add_ai_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column("media_assets", sa.Column("embedding", Vector(512), nullable=True))
    op.create_table(
        "media_embedding_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("media_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("modality", sa.String(length=24), nullable=False),
        sa.Column("source_start", sa.Numeric(12, 3), nullable=False),
        sa.Column("source_end", sa.Numeric(12, 3), nullable=False),
        sa.Column("embedding", Vector(512), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["media_asset_id"], ["media_assets.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_media_embedding_segments_media_asset_id", "media_embedding_segments", ["media_asset_id"])
    op.create_index("ix_media_embedding_segments_modality", "media_embedding_segments", ["modality"])
    op.execute("CREATE INDEX ix_media_assets_embedding_hnsw ON media_assets USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL")
    op.execute("CREATE INDEX ix_media_embedding_segments_embedding_hnsw ON media_embedding_segments USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_media_embedding_segments_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_media_assets_embedding_hnsw")
    op.drop_table("media_embedding_segments")
    op.drop_column("media_assets", "embedding")
