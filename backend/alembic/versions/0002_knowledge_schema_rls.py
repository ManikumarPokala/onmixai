"""Knowledge schema (collections, permissions, documents, chunks) with forced RLS.

All four tenant tables get RLS + FORCE ROW LEVEL SECURITY and the tenant_isolation
policy in this same migration (CLAUDE.md §4). Adds org document quota. The chunk
vector column is sized from get_embedding_dimension() — the single source of truth.
Role-agnostic: the runtime role's access comes from the Sprint 1 default
privileges, exercised for real here.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op
from src.shared.config import get_embedding_dimension

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = ("collections", "collection_permissions", "documents", "chunks")
_TIMESTAMP = sa.DateTime(timezone=True)

collection_permission = postgresql.ENUM(
    "read", "write", "manage", name="collection_permission", create_type=False
)
document_status = postgresql.ENUM(
    "queued", "processing", "ready", "failed", name="document_status", create_type=False
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    collection_permission.create(op.get_bind(), checkfirst=True)
    document_status.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "organizations",
        sa.Column("max_documents", sa.Integer(), nullable=False, server_default="500"),
    )

    op.create_table(
        "collections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", _TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", _TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_collections"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_collections_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_collections_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("org_id", "name", name="uq_collections_org_id_name"),
    )
    op.create_index("ix_collections_org_id", "collections", ["org_id"])

    op.create_table(
        "collection_permissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("permission", collection_permission, nullable=False),
        sa.Column("created_at", _TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_collection_permissions"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_collection_permissions_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collections.id"],
            name="fk_collection_permissions_collection_id_collections",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_collection_permissions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "collection_id", "user_id", name="uq_collection_permissions_collection_id_user_id"
        ),
    )
    op.create_index("ix_collection_permissions_org_id", "collection_permissions", ["org_id"])
    op.create_index(
        "ix_collection_permissions_collection_id", "collection_permissions", ["collection_id"]
    )
    op.create_index("ix_collection_permissions_user_id", "collection_permissions", ["user_id"])

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=1024), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("status", document_status, nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("claimed_at", _TIMESTAMP, nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", _TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", _TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_documents_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collections.id"],
            name="fk_documents_collection_id_collections",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["documents.id"],
            name="fk_documents_supersedes_id_documents",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_documents_created_by_users", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("storage_key", name="uq_documents_storage_key"),
    )
    op.create_index(
        "ix_documents_org_id_collection_id_status",
        "documents",
        ["org_id", "collection_id", "status"],
    )
    op.create_index("ix_documents_status_claimed_at", "documents", ["status", "claimed_at"])

    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("embedding", Vector(get_embedding_dimension()), nullable=True),
        sa.Column("created_at", _TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_chunks"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_chunks_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_chunks_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "document_id", "content_hash", name="uq_chunks_document_id_content_hash"
        ),
    )
    op.create_index("ix_chunks_org_id_document_id_seq", "chunks", ["org_id", "document_id", "seq"])

    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} FOR ALL "
            "USING (org_id = current_setting('app.current_org_id', true)::uuid) "
            "WITH CHECK (org_id = current_setting('app.current_org_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in _TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")

    op.drop_index("ix_chunks_org_id_document_id_seq", table_name="chunks")
    op.drop_table("chunks")
    op.drop_index("ix_documents_status_claimed_at", table_name="documents")
    op.drop_index("ix_documents_org_id_collection_id_status", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_collection_permissions_user_id", table_name="collection_permissions")
    op.drop_index("ix_collection_permissions_collection_id", table_name="collection_permissions")
    op.drop_index("ix_collection_permissions_org_id", table_name="collection_permissions")
    op.drop_table("collection_permissions")
    op.drop_index("ix_collections_org_id", table_name="collections")
    op.drop_table("collections")

    op.drop_column("organizations", "max_documents")

    document_status.drop(op.get_bind(), checkfirst=True)
    collection_permission.drop(op.get_bind(), checkfirst=True)
    op.execute("DROP EXTENSION IF EXISTS vector")
