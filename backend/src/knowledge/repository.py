"""Knowledge repositories — the only place knowledge queries live (patterns.md §2).

Every method is tenant-scoped; results are ORM models / None with no business
decisions. Application org-scoping sits alongside Postgres RLS (defense in depth).
"""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, Select, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.knowledge.models import (
    Chunk,
    Collection,
    CollectionPermission,
    Document,
    DocumentStatus,
    Permission,
    StorageDeletionOutbox,
)
from src.knowledge.schemas import ChunkCandidate, RetrievalFilters

_LIST_CAP = 100


class CollectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, collection: Collection) -> Collection:
        self._session.add(collection)
        await self._session.flush()
        await self._session.refresh(collection)
        return collection

    async def get(self, org_id: UUID, collection_id: UUID) -> Collection | None:
        stmt = select(Collection).where(Collection.org_id == org_id, Collection.id == collection_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_name(self, org_id: UUID, name: str) -> Collection | None:
        stmt = select(Collection).where(Collection.org_id == org_id, Collection.name == name)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_readable(
        self, org_id: UUID, user_id: UUID, *, cursor: UUID | None = None, limit: int = 50
    ) -> list[Collection]:
        """Collections the user has any permission on, cursor-paginated. Time: O(limit)."""
        stmt = (
            select(Collection)
            .join(CollectionPermission, CollectionPermission.collection_id == Collection.id)
            .where(Collection.org_id == org_id, CollectionPermission.user_id == user_id)
            .order_by(Collection.id)
            .limit(min(limit, _LIST_CAP))
        )
        if cursor is not None:
            stmt = stmt.where(Collection.id > cursor)
        return list((await self._session.execute(stmt)).scalars())

    async def get_permission(
        self, org_id: UUID, collection_id: UUID, user_id: UUID
    ) -> CollectionPermission | None:
        stmt = select(CollectionPermission).where(
            CollectionPermission.org_id == org_id,
            CollectionPermission.collection_id == collection_id,
            CollectionPermission.user_id == user_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert_permission(
        self, org_id: UUID, collection_id: UUID, user_id: UUID, permission: Permission
    ) -> None:
        """Grant or update a user's permission on a collection (idempotent)."""
        stmt = insert(CollectionPermission).values(
            org_id=org_id,
            collection_id=collection_id,
            user_id=user_id,
            permission=permission,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_collection_permissions_collection_id_user_id",
            set_={"permission": permission},
        )
        await self._session.execute(stmt)

    async def delete(self, org_id: UUID, collection_id: UUID) -> None:
        """Delete an (already-verified-empty) collection. Time: O(1)."""
        await self._session.execute(
            delete(Collection).where(Collection.org_id == org_id, Collection.id == collection_id)
        )


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, document: Document) -> Document:
        self._session.add(document)
        await self._session.flush()
        await self._session.refresh(document)
        return document

    async def get(self, org_id: UUID, document_id: UUID) -> Document | None:
        stmt = select(Document).where(Document.org_id == org_id, Document.id == document_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_by_collection(
        self, org_id: UUID, collection_id: UUID, *, cursor: UUID | None = None, limit: int = 50
    ) -> list[Document]:
        stmt = (
            select(Document)
            .where(Document.org_id == org_id, Document.collection_id == collection_id)
            .order_by(Document.id)
            .limit(min(limit, _LIST_CAP))
        )
        if cursor is not None:
            stmt = stmt.where(Document.id > cursor)
        return list((await self._session.execute(stmt)).scalars())

    async def count_for_org(self, org_id: UUID) -> int:
        stmt = select(func.count()).select_from(Document).where(Document.org_id == org_id)
        return int((await self._session.execute(stmt)).scalar_one())

    async def count_for_collection(self, org_id: UUID, collection_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Document)
            .where(Document.org_id == org_id, Document.collection_id == collection_id)
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def claim_for_processing(self, org_id: UUID, document_id: UUID, now: datetime) -> bool:
        """Atomically claim a QUEUED document (compare-and-set). Returns True iff won.

        Concurrent duplicate deliveries: exactly one UPDATE matches status='queued';
        the loser gets rowcount 0 and backs off (patterns.md §3/§7). Time: O(1).
        """
        stmt = (
            update(Document)
            .where(
                Document.org_id == org_id,
                Document.id == document_id,
                Document.status == DocumentStatus.QUEUED,
            )
            .values(
                status=DocumentStatus.PROCESSING,
                claimed_at=now,
                attempt_count=Document.attempt_count + 1,
            )
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        return result.rowcount == 1

    async def mark_ready(
        self, org_id: UUID, document_id: UUID, *, page_count: int | None = None
    ) -> None:
        """processing → ready (compare-and-set), clearing any prior failure reason."""
        stmt = (
            update(Document)
            .where(
                Document.org_id == org_id,
                Document.id == document_id,
                Document.status == DocumentStatus.PROCESSING,
            )
            .values(status=DocumentStatus.READY, failure_reason=None, page_count=page_count)
        )
        await self._session.execute(stmt)

    async def mark_failed(self, org_id: UUID, document_id: UUID, reason: str) -> None:
        """Terminal failure with a user-visible reason."""
        stmt = (
            update(Document)
            .where(Document.org_id == org_id, Document.id == document_id)
            .values(status=DocumentStatus.FAILED, failure_reason=reason)
        )
        await self._session.execute(stmt)

    async def requeue(self, org_id: UUID, document_id: UUID) -> None:
        """processing → queued (retry / sweeper recovery), clearing the claim."""
        stmt = (
            update(Document)
            .where(
                Document.org_id == org_id,
                Document.id == document_id,
                Document.status == DocumentStatus.PROCESSING,
            )
            .values(status=DocumentStatus.QUEUED, claimed_at=None)
        )
        await self._session.execute(stmt)

    async def enqueue_reindex(self, org_id: UUID, document_id: UUID) -> bool:
        """ready → queued for re-index (compare-and-set on READY). Returns True iff
        the document was READY and is now QUEUED. Time: O(1)."""
        stmt = (
            update(Document)
            .where(
                Document.org_id == org_id,
                Document.id == document_id,
                Document.status == DocumentStatus.READY,
            )
            .values(status=DocumentStatus.QUEUED, claimed_at=None)
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        return result.rowcount == 1

    async def mark_superseded(self, org_id: UUID, document_id: UUID) -> None:
        """Flag a prior version replaced (its chunks are removed separately)."""
        await self._session.execute(
            update(Document)
            .where(Document.org_id == org_id, Document.id == document_id)
            .values(superseded=True)
        )

    async def delete(self, org_id: UUID, document_id: UUID) -> None:
        """Delete a document; its chunks cascade via the FK. Time: O(chunks)."""
        await self._session.execute(
            delete(Document).where(Document.org_id == org_id, Document.id == document_id)
        )

    async def list_stuck(self, org_id: UUID, claimed_before: datetime) -> list[Document]:
        """Documents stuck in PROCESSING past the deadline (dead worker)."""
        stmt = select(Document).where(
            Document.org_id == org_id,
            Document.status == DocumentStatus.PROCESSING,
            Document.claimed_at < claimed_before,
        )
        return list((await self._session.execute(stmt)).scalars())


class ChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_embedded(self, rows: list[dict[str, Any]]) -> None:
        """Bulk-insert embedded chunks, skipping any whose (document_id,
        content_hash) already exists (patterns.md §7 — deterministic upsert).

        One INSERT per call, so a document of n chunks costs O(batches) statements,
        never O(n) (performance.md §5). Re-running with the same content inserts
        zero rows and leaves existing embeddings untouched. Time: O(rows) in one
        statement. Space: O(rows).
        """
        if not rows:
            return
        statement = (
            insert(Chunk)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["document_id", "content_hash"])
        )
        await self._session.execute(statement)

    async def hashes_for_document(self, org_id: UUID, document_id: UUID) -> set[str]:
        stmt = select(Chunk.content_hash).where(
            Chunk.org_id == org_id, Chunk.document_id == document_id
        )
        return set((await self._session.execute(stmt)).scalars())

    async def count_for_document(self, org_id: UUID, document_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.org_id == org_id, Chunk.document_id == document_id)
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def delete_for_document(self, org_id: UUID, document_id: UUID) -> None:
        """Remove all of a document's chunks (used when superseding). Time: O(chunks)."""
        await self._session.execute(
            delete(Chunk).where(Chunk.org_id == org_id, Chunk.document_id == document_id)
        )

    async def delete_stale(self, org_id: UUID, document_id: UUID, keep_hashes: set[str]) -> None:
        """Delete chunks whose content hash is no longer produced (re-index cleanup).

        With upsert_embedded (insert new) this makes a rebuild converge to exactly
        the current chunk set. Time: O(chunks). Space: O(len(keep_hashes))."""
        stmt = delete(Chunk).where(Chunk.org_id == org_id, Chunk.document_id == document_id)
        if keep_hashes:
            stmt = stmt.where(Chunk.content_hash.not_in(keep_hashes))
        await self._session.execute(stmt)

    def _candidate_select(
        self, org_id: UUID, user_id: UUID, filters: RetrievalFilters, score: Any
    ) -> Select[Any]:
        """Chunk columns + a ``score`` expression, with the org_id + collection-ACL
        predicate and metadata narrowing applied BEFORE any ranking (CLAUDE.md §4).

        The ACL is an EXISTS over collection_permissions for ``user_id`` on the
        chunk's document collection; a chunk the user cannot access is never a
        candidate. Time/Space: O(1) to build.
        """
        has_permission = (
            select(CollectionPermission.id)
            .where(
                CollectionPermission.org_id == org_id,
                CollectionPermission.collection_id == Document.collection_id,
                CollectionPermission.user_id == user_id,
            )
            .exists()
        )
        stmt = (
            select(
                Chunk.id.label("chunk_id"),
                Chunk.document_id.label("document_id"),
                Document.collection_id.label("collection_id"),
                Document.filename.label("filename"),
                Chunk.content.label("content"),
                Chunk.chunk_metadata.label("ref"),
                score.label("score"),
            )
            .join(Document, Document.id == Chunk.document_id)
            .where(
                Chunk.org_id == org_id,
                Chunk.embedding.isnot(None),
                Document.superseded.is_(False),
                has_permission,
            )
        )
        if filters.collection_id is not None:
            stmt = stmt.where(Document.collection_id == filters.collection_id)
        if filters.content_type is not None:
            stmt = stmt.where(Document.content_type == filters.content_type)
        if filters.created_after is not None:
            stmt = stmt.where(Document.created_at >= filters.created_after)
        if filters.created_before is not None:
            stmt = stmt.where(Document.created_at <= filters.created_before)
        return stmt

    def vector_select(
        self,
        org_id: UUID,
        user_id: UUID,
        embedding: list[float],
        filters: RetrievalFilters,
        top_k: int,
    ) -> Select[Any]:
        """The ACL-filtered nearest-neighbour statement (cosine distance, HNSW)."""
        distance = Chunk.embedding.cosine_distance(embedding)
        return (
            self._candidate_select(org_id, user_id, filters, score=1 - distance)
            .order_by(distance)
            .limit(top_k)
        )

    def keyword_select(
        self,
        org_id: UUID,
        user_id: UUID,
        query: str,
        language: str,
        filters: RetrievalFilters,
        top_k: int,
    ) -> Select[Any]:
        """The ACL-filtered full-text statement (websearch_to_tsquery, GIN, ts_rank)."""
        tsquery = func.websearch_to_tsquery(language, query)
        rank = func.ts_rank(Chunk.content_tsv, tsquery)
        return (
            self._candidate_select(org_id, user_id, filters, score=rank)
            .where(Chunk.content_tsv.op("@@")(tsquery))
            .order_by(rank.desc())
            .limit(top_k)
        )

    async def search_vector(
        self,
        org_id: UUID,
        user_id: UUID,
        *,
        embedding: list[float],
        filters: RetrievalFilters,
        top_k: int,
        ef_search: int,
    ) -> list[ChunkCandidate]:
        """Nearest chunks by cosine distance via the HNSW index, ACL-filtered first.

        Time: O(log n) HNSW probe + O(top_k). Space: O(top_k). n = org's chunks.
        """
        await self._session.execute(
            text("SELECT set_config('hnsw.ef_search', :ef, true)"), {"ef": str(ef_search)}
        )
        stmt = self.vector_select(org_id, user_id, embedding, filters, top_k)
        return [_to_candidate(row) for row in (await self._session.execute(stmt)).all()]

    async def search_keyword(
        self,
        org_id: UUID,
        user_id: UUID,
        *,
        query: str,
        language: str,
        filters: RetrievalFilters,
        top_k: int,
    ) -> list[ChunkCandidate]:
        """Top chunks by full-text rank via the GIN index, ACL-filtered first.

        Time: O(matches · log matches). Space: O(top_k).
        """
        stmt = self.keyword_select(org_id, user_id, query, language, filters, top_k)
        return [_to_candidate(row) for row in (await self._session.execute(stmt)).all()]


def _to_candidate(row: Any) -> ChunkCandidate:
    return ChunkCandidate(
        chunk_id=row.chunk_id,
        document_id=row.document_id,
        collection_id=row.collection_id,
        filename=row.filename,
        content=row.content,
        ref=row.ref or {},
        score=float(row.score),
    )


class StorageOutboxRepository:
    """Queries for the storage-deletion outbox (compensation log)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, org_id: UUID, storage_key: str) -> None:
        self._session.add(StorageDeletionOutbox(org_id=org_id, storage_key=storage_key))
        await self._session.flush()

    async def list_pending(self, org_id: UUID, *, limit: int = 100) -> list[StorageDeletionOutbox]:
        stmt = (
            select(StorageDeletionOutbox)
            .where(StorageDeletionOutbox.org_id == org_id)
            .order_by(StorageDeletionOutbox.created_at)
            .limit(min(limit, _LIST_CAP))
        )
        return list((await self._session.execute(stmt)).scalars())

    async def delete(self, org_id: UUID, outbox_id: UUID) -> None:
        await self._session.execute(
            delete(StorageDeletionOutbox).where(
                StorageDeletionOutbox.org_id == org_id, StorageDeletionOutbox.id == outbox_id
            )
        )

    async def bump_attempts(self, org_id: UUID, outbox_id: UUID) -> None:
        await self._session.execute(
            update(StorageDeletionOutbox)
            .where(StorageDeletionOutbox.org_id == org_id, StorageDeletionOutbox.id == outbox_id)
            .values(attempts=StorageDeletionOutbox.attempts + 1)
        )
