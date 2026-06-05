"""Knowledge domain ORM models: collections, permissions, documents, chunks.

All four tables are tenant-owned (``org_id NOT NULL``) and protected by forced
Row-Level Security created in migration 0002 (CLAUDE.md §4). The chunk vector
column is sized from the single source of truth, ``get_embedding_dimension()``.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from src.shared.config import get_embedding_dimension, get_index_params
from src.shared.database import Base

PERMISSION_ENUM_NAME = "collection_permission"
DOCUMENT_STATUS_ENUM_NAME = "document_status"

# Vector width: the single source of truth (settings), read once at import.
_EMBEDDING_DIMENSION = get_embedding_dimension()
# HNSW/FTS build params: the same single source the migration reads (CLAUDE.md §7).
_INDEX_PARAMS = get_index_params()


class Permission(StrEnum):
    """Collection access level. Ordered read < write < manage (see rules.py)."""

    READ = "read"
    WRITE = "write"
    MANAGE = "manage"


class DocumentStatus(StrEnum):
    """Lifecycle state of a document in the ingestion pipeline."""

    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class Collection(Base):
    """A named grouping of documents within a tenant."""

    __tablename__ = "collections"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_collections_org_id_name"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CollectionPermission(Base):
    """A per-user access grant on a collection (groundwork for Phase 2 ACLs)."""

    __tablename__ = "collection_permissions"
    __table_args__ = (
        UniqueConstraint(
            "collection_id", "user_id", name="uq_collection_permissions_collection_id_user_id"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    collection_id: Mapped[UUID] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    permission: Mapped[Permission] = mapped_column(
        Enum(Permission, name=PERMISSION_ENUM_NAME, values_callable=lambda e: [m.value for m in e])
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    """An uploaded source document moving through the ingestion state machine."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("storage_key", name="uq_documents_storage_key"),
        Index("ix_documents_org_id_collection_id_status", "org_id", "collection_id", "status"),
        Index("ix_documents_status_claimed_at", "status", "claimed_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    collection_id: Mapped[UUID] = mapped_column(ForeignKey("collections.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(1024))
    content_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(String(1024))
    content_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    supersedes_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    # A version that has been replaced: kept for history (status stays READY) but
    # its chunks are removed once the newer version is READY, so retrieval (Phase 2)
    # never sees two live versions of the same document. See ADR 0007.
    superseded: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(
            DocumentStatus,
            name=DOCUMENT_STATUS_ENUM_NAME,
            values_callable=lambda e: [m.value for m in e],
        )
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Chunk(Base):
    """A unit of a document's content with its embedding (filled during ingest)."""

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "content_hash", name="uq_chunks_document_id_content_hash"),
        Index("ix_chunks_org_id_document_id_seq", "org_id", "document_id", "seq"),
        # Hybrid-retrieval indexes (migration 0004). HNSW for the vector arm (cosine),
        # GIN for the FTS keyword arm. Build params come from the single source.
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={
                "m": _INDEX_PARAMS.hnsw_m,
                "ef_construction": _INDEX_PARAMS.hnsw_ef_construction,
            },
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_content_tsv_gin", "content_tsv", postgresql_using="gin"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    token_count: Mapped[int] = mapped_column(Integer)
    # ``metadata`` is reserved by SQLAlchemy Declarative, so map a renamed attribute.
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(_EMBEDDING_DIMENSION), nullable=True
    )
    # Postgres FTS vector for the keyword retrieval arm — generated/STORED from
    # ``content`` by the DB (never written by the app). Language is the single-source
    # config (migration 0004 builds the identical expression + a GIN index).
    content_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(f"to_tsvector('{_INDEX_PARAMS.fts_language}', content)", persisted=True),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StorageDeletionOutbox(Base):
    """Durable intent to delete a storage object (transactional outbox).

    A document delete removes the row (chunks cascade) and records the object's key
    here in the same transaction; the object is then deleted best-effort after
    commit, and a sweeper retries any rows left behind — so a crash between the DB
    commit and the storage delete can never orphan an object. See the domain README.
    """

    __tablename__ = "storage_deletion_outbox"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    storage_key: Mapped[str] = mapped_column(String(1024))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
