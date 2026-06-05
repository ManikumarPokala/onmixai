"""Hybrid-retrieval indexes on chunks: HNSW (vector) + FTS tsvector/GIN (keyword).

Adds the generated ``content_tsv`` column and the two retrieval indexes that the
Phase 2 search arms depend on. Build params (HNSW m / ef_construction, FTS language)
come from ``get_index_params()`` — the single source of truth shared with the ORM
model (CLAUDE.md §7). RLS already covers ``chunks`` (migration 0002); no policy
change. The vector extension was created in 0002.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from src.shared.config import get_index_params

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PARAMS = get_index_params()


def upgrade() -> None:
    op.add_column(
        "chunks",
        sa.Column(
            "content_tsv",
            postgresql.TSVECTOR(),
            sa.Computed(f"to_tsvector('{_PARAMS.fts_language}', content)", persisted=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={
            "m": _PARAMS.hnsw_m,
            "ef_construction": _PARAMS.hnsw_ef_construction,
        },
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_chunks_content_tsv_gin",
        "chunks",
        ["content_tsv"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_chunks_content_tsv_gin", table_name="chunks")
    op.drop_index("ix_chunks_embedding_hnsw", table_name="chunks")
    op.drop_column("chunks", "content_tsv")
