"""Knowledge repositories — the only place knowledge queries live (patterns.md §2).

Every method is tenant-scoped; results are ORM models / None with no business
decisions. Application org-scoping sits alongside Postgres RLS (defense in depth).
"""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.knowledge.models import (
    Chunk,
    Collection,
    CollectionPermission,
    Document,
    DocumentStatus,
    Permission,
)

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
