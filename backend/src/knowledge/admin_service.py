"""Knowledge administration — the privileged, ACL-bypassing counterpart to KnowledgeService.

An owner/admin manages every document in their org regardless of per-collection ACLs (the user
surface enforces those; this surface does not). It stays org-scoped (RLS + the org_id predicate)
and audits every mutation under ``admin.*`` actions so the privileged path is distinguishable in
the audit log. Kept as a separate class so the ACL-skipping methods can never be reached through
the user-facing service by mistake (CLAUDE.md §1: one established way per use case).
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.schemas import AuthContext
from src.knowledge.exceptions import DocumentNotFoundError, InvalidStatusTransitionError
from src.knowledge.repository import DocumentRepository, StorageOutboxRepository
from src.knowledge.rules import ensure_document_deletable
from src.knowledge.schemas import DocumentDTO, DocumentPage, DocumentResponse, QuotaUsage
from src.knowledge.service import OrgQuotaReader
from src.shared.audit import AuditEmitter
from src.shared.config import Settings
from src.shared.database import register_after_commit
from src.shared.pagination import decode_keyset_cursor, encode_keyset_cursor
from src.shared.queue import JobQueue
from src.shared.storage import ObjectStorage


class KnowledgeAdminService:
    """Owner/admin knowledge administration (CLAUDE.md §3.1: 6-step methods). Cross-collection,
    org-scoped, every mutation audited. Cross-org documents are invisible (404, never an oracle)."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        documents: DocumentRepository,
        outbox: StorageOutboxRepository,
        storage: ObjectStorage,
        queue: JobQueue,
        audit: AuditEmitter,
        quota_reader: OrgQuotaReader,
        settings: Settings,
    ) -> None:
        self._session = session
        self._documents = documents
        self._outbox = outbox
        self._storage = storage
        self._queue = queue
        self._audit = audit
        self._quota_reader = quota_reader
        self._settings = settings

    async def list_documents(
        self, actor: AuthContext, *, cursor: str | None, limit: int
    ) -> DocumentPage:
        """One newest-first page of every document in the org, across collections. Server-capped.
        Time: O(limit). Raises INVALID_CURSOR."""
        capped = min(limit, self._settings.admin_document_page_size)
        before = decode_keyset_cursor(cursor) if cursor is not None else None
        rows = await self._documents.list_for_org(actor.org_id, limit=capped + 1, before=before)
        has_more = len(rows) > capped
        page = rows[:capped]
        next_cursor = encode_keyset_cursor(page[-1].created_at, page[-1].id) if has_more else None
        return DocumentPage(
            documents=[DocumentResponse.from_dto(DocumentDTO.from_model(d)) for d in page],
            next_cursor=next_cursor,
        )

    async def quota_usage(self, actor: AuthContext) -> QuotaUsage:
        """The org's document quota usage (used / limit / remaining). Time: O(1)."""
        used = await self._documents.count_for_org(actor.org_id)
        limit = await self._quota_reader.get_document_quota(actor.org_id)
        return QuotaUsage(used=used, limit=limit, remaining=max(0, limit - used))

    async def reindex_document(self, actor: AuthContext, document_id: UUID) -> None:
        """Force-requeue a document for an idempotent chunk/embedding rebuild (audited). No ACL
        check — admin acts on any doc in the org. Time: O(1)."""
        document = await self._documents.get(actor.org_id, document_id)
        if document is None:
            raise DocumentNotFoundError()
        if not await self._documents.enqueue_reindex(actor.org_id, document_id):
            raise InvalidStatusTransitionError(detail=f"{document.status.value} -> queued")
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="admin.document_reindexed",
            resource_type="document",
            resource_id=document_id,
        )
        org_id = actor.org_id
        register_after_commit(
            self._session,
            lambda: self._queue.enqueue_ingest(document_id=document_id, org_id=org_id),
        )

    async def delete_document(self, actor: AuthContext, document_id: UUID) -> None:
        """Delete any document in the org (chunks cascade) and compensate storage (audited). No
        ACL check. Time: O(1)."""
        document = await self._documents.get(actor.org_id, document_id)
        if document is None:
            raise DocumentNotFoundError()
        ensure_document_deletable(document.status)
        storage_key = document.storage_key
        await self._documents.delete(actor.org_id, document_id)
        await self._outbox.add(actor.org_id, storage_key)  # durable delete intent
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="admin.document_deleted",
            resource_type="document",
            resource_id=document_id,
        )
        register_after_commit(self._session, lambda: self._storage.delete(storage_key))
