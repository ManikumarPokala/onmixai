"""Knowledge service — collections, ACLs, and streaming quota-enforced upload.

Service methods follow the patterns.md §1 anatomy and own no transaction. Upload
streams to object storage computing the content hash incrementally (never
buffering the file), enforces the size limit mid-stream, and enqueues ingestion
only after the request commits (so the worker never races an uncommitted row).
"""

import hashlib
from collections.abc import AsyncIterator
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.schemas import AuthContext
from src.knowledge.exceptions import (
    CollectionAccessDeniedError,
    CollectionNameTakenError,
    CollectionNotFoundError,
    DocumentNotFoundError,
    InvalidStatusTransitionError,
    UploadTooLargeError,
)
from src.knowledge.models import Collection, Document, DocumentStatus, Permission
from src.knowledge.repository import (
    ChunkRepository,
    CollectionRepository,
    DocumentRepository,
    StorageOutboxRepository,
)
from src.knowledge.rules import (
    ensure_collection_empty,
    ensure_collection_permission,
    ensure_document_deletable,
    ensure_upload_acceptable,
    ensure_within_quota,
)
from src.knowledge.schemas import (
    ChunkCandidate,
    CollectionDTO,
    DocumentDTO,
    RetrievalFilters,
    UploadAccepted,
)
from src.shared.audit import AuditEmitter
from src.shared.config import Settings
from src.shared.database import register_after_commit
from src.shared.queue import JobQueue
from src.shared.storage import ObjectStorage

_UPLOAD_CHUNK = 1024 * 1024


class OrgQuotaReader(Protocol):
    """The org-quota port knowledge depends on; identity's OrgPolicyService
    satisfies it structurally (CLAUDE.md §3.3 — cross-domain via interface)."""

    async def get_document_quota(self, org_id: UUID) -> int: ...


class KnowledgeService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        collections: CollectionRepository,
        documents: DocumentRepository,
        chunks: ChunkRepository,
        outbox: StorageOutboxRepository,
        storage: ObjectStorage,
        queue: JobQueue,
        audit: AuditEmitter,
        quota_reader: OrgQuotaReader,
        settings: Settings,
    ) -> None:
        self._session = session
        self._collections = collections
        self._documents = documents
        self._chunks = chunks
        self._outbox = outbox
        self._storage = storage
        self._queue = queue
        self._audit = audit
        self._quota_reader = quota_reader
        self._settings = settings

    async def create_collection(
        self, actor: AuthContext, *, name: str, description: str | None
    ) -> CollectionDTO:
        """Create a collection; the creator gets MANAGE automatically."""
        if await self._collections.get_by_name(actor.org_id, name) is not None:
            raise CollectionNameTakenError(detail=name)
        collection = await self._collections.create(
            Collection(
                org_id=actor.org_id, name=name, description=description, created_by=actor.user_id
            )
        )
        await self._collections.upsert_permission(
            actor.org_id, collection.id, actor.user_id, Permission.MANAGE
        )
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="collection.created",
            resource_id=collection.id,
        )
        return CollectionDTO.from_model(collection)

    async def list_collections(self, actor: AuthContext) -> list[CollectionDTO]:
        collections = await self._collections.list_readable(actor.org_id, actor.user_id)
        return [CollectionDTO.from_model(c) for c in collections]

    async def grant_permission(
        self, actor: AuthContext, *, collection_id: UUID, user_id: UUID, permission: Permission
    ) -> None:
        """Grant a user a permission on a collection (requires MANAGE)."""
        if await self._collections.get(actor.org_id, collection_id) is None:
            raise CollectionNotFoundError()
        await self._require_permission(actor, collection_id, Permission.MANAGE)
        await self._collections.upsert_permission(actor.org_id, collection_id, user_id, permission)
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="collection.permission_granted",
            resource_id=collection_id,
            grantee=str(user_id),
            permission=permission.value,
        )

    async def upload_document(
        self,
        actor: AuthContext,
        *,
        collection_id: UUID,
        filename: str,
        content_type: str,
        declared_size: int,
        source: AsyncIterator[bytes],
    ) -> UploadAccepted:
        """Stream an upload to storage, create a QUEUED document, enqueue ingest."""
        if await self._collections.get(actor.org_id, collection_id) is None:
            raise CollectionNotFoundError()
        await self._require_permission(actor, collection_id, Permission.WRITE)
        document = await self._store_document(
            actor,
            collection_id=collection_id,
            filename=filename,
            content_type=content_type,
            declared_size=declared_size,
            source=source,
            version=1,
            supersedes_id=None,
        )
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="document.uploaded",
            resource_id=document.id,
        )
        return UploadAccepted(document_id=document.id, status=document.status)

    async def create_version(
        self,
        actor: AuthContext,
        *,
        document_id: UUID,
        filename: str,
        content_type: str,
        declared_size: int,
        source: AsyncIterator[bytes],
    ) -> UploadAccepted:
        """Upload a new version of a document; ingest supersedes the prior on READY."""
        original = await self._documents.get(actor.org_id, document_id)
        if original is None:
            raise DocumentNotFoundError()
        await self._require_permission(actor, original.collection_id, Permission.WRITE)
        version = await self._store_document(
            actor,
            collection_id=original.collection_id,
            filename=filename,
            content_type=content_type,
            declared_size=declared_size,
            source=source,
            version=original.version + 1,
            supersedes_id=original.id,
        )
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="document.version_created",
            resource_id=version.id,
            supersedes=str(original.id),
        )
        return UploadAccepted(document_id=version.id, status=version.status)

    async def delete_document(self, actor: AuthContext, document_id: UUID) -> None:
        """Delete a document (chunks cascade) and compensate the storage object."""
        document = await self._documents.get(actor.org_id, document_id)
        if document is None:
            raise DocumentNotFoundError()
        await self._require_permission(actor, document.collection_id, Permission.WRITE)
        ensure_document_deletable(document.status)
        storage_key = document.storage_key
        await self._documents.delete(actor.org_id, document_id)
        await self._outbox.add(actor.org_id, storage_key)  # durable delete intent
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="document.deleted",
            resource_id=document_id,
        )
        # Best-effort delete after commit; on failure the outbox row drives a retry.
        register_after_commit(self._session, lambda: self._storage.delete(storage_key))

    async def reindex_document(self, actor: AuthContext, document_id: UUID) -> None:
        """Re-queue a READY document for an idempotent chunk/embedding rebuild."""
        document = await self._documents.get(actor.org_id, document_id)
        if document is None:
            raise DocumentNotFoundError()
        await self._require_permission(actor, document.collection_id, Permission.MANAGE)
        if not await self._documents.enqueue_reindex(actor.org_id, document_id):
            raise InvalidStatusTransitionError(detail=f"{document.status.value} -> queued")
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="document.reindex_requested",
            resource_id=document_id,
        )
        org_id = actor.org_id
        register_after_commit(
            self._session,
            lambda: self._queue.enqueue_ingest(document_id=document_id, org_id=org_id),
        )

    async def delete_collection(self, actor: AuthContext, collection_id: UUID) -> None:
        """Delete an empty collection (non-empty → ConflictError; bulk cascade later)."""
        if await self._collections.get(actor.org_id, collection_id) is None:
            raise CollectionNotFoundError()
        await self._require_permission(actor, collection_id, Permission.MANAGE)
        ensure_collection_empty(
            await self._documents.count_for_collection(actor.org_id, collection_id)
        )
        await self._collections.delete(actor.org_id, collection_id)
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="collection.deleted",
            resource_id=collection_id,
        )

    async def get_document(self, actor: AuthContext, document_id: UUID) -> DocumentDTO:
        document = await self._documents.get(actor.org_id, document_id)
        if document is None:
            raise DocumentNotFoundError()
        await self._require_permission(actor, document.collection_id, Permission.READ)
        return DocumentDTO.from_model(document)

    async def _store_document(
        self,
        actor: AuthContext,
        *,
        collection_id: UUID,
        filename: str,
        content_type: str,
        declared_size: int,
        source: AsyncIterator[bytes],
        version: int,
        supersedes_id: UUID | None,
    ) -> Document:
        """Shared ingest entry: validate, stream to storage, create QUEUED row,
        enqueue after commit. Caller authorizes and audits. Time: O(file size)."""
        ensure_upload_acceptable(declared_size, content_type, self._settings.max_upload_bytes)
        ensure_within_quota(
            await self._documents.count_for_org(actor.org_id),
            await self._quota_reader.get_document_quota(actor.org_id),
        )
        storage_key = f"org/{actor.org_id}/doc/{uuid4()}"
        hasher = hashlib.sha256()
        stored = await self._storage.put_stream(
            storage_key, self._hash_and_limit(source, hasher), content_type
        )
        document = await self._documents.create(
            Document(
                org_id=actor.org_id,
                collection_id=collection_id,
                filename=filename,
                content_type=content_type,
                size_bytes=stored.size_bytes,
                storage_key=storage_key,
                content_hash=hasher.hexdigest(),
                version=version,
                supersedes_id=supersedes_id,
                status=DocumentStatus.QUEUED,
                created_by=actor.user_id,
            )
        )
        document_id, org_id = document.id, actor.org_id
        register_after_commit(
            self._session,
            lambda: self._queue.enqueue_ingest(document_id=document_id, org_id=org_id),
        )
        return document

    async def _require_permission(
        self, actor: AuthContext, collection_id: UUID, required: Permission
    ) -> None:
        permission = await self._collections.get_permission(
            actor.org_id, collection_id, actor.user_id
        )
        if permission is None:
            raise CollectionAccessDeniedError(detail=f"requires {required.value}")
        ensure_collection_permission(permission.permission, required)

    async def _hash_and_limit(
        self, source: AsyncIterator[bytes], hasher: "hashlib._Hash"
    ) -> AsyncIterator[bytes]:
        """Pass bytes through, hashing and enforcing the size cap mid-stream.

        Raising here aborts the in-progress storage upload (the adapter cancels
        the multipart upload), so an oversize stream leaves no orphan object.
        """
        total = 0
        max_bytes = self._settings.max_upload_bytes
        async for chunk in source:
            total += len(chunk)
            if total > max_bytes:
                raise UploadTooLargeError(detail=f"exceeds {max_bytes} bytes")
            hasher.update(chunk)
            yield chunk


class ChunkRetrievalService:
    """Knowledge's retrieval interface — satisfies search's ChunkCandidateReader
    port structurally (CLAUDE.md §3.3). The two arms are ACL-filtered in the SQL
    predicate; the FTS language is knowledge's index property (the same one the
    tsvector column was built with), so the keyword arm sources it from settings."""

    def __init__(self, chunks: ChunkRepository, settings: Settings) -> None:
        self._chunks = chunks
        self._settings = settings

    async def vector_candidates(
        self,
        org_id: UUID,
        user_id: UUID,
        *,
        embedding: list[float],
        filters: RetrievalFilters,
        top_k: int,
        ef_search: int,
    ) -> list[ChunkCandidate]:
        return await self._chunks.search_vector(
            org_id,
            user_id,
            embedding=embedding,
            filters=filters,
            top_k=top_k,
            ef_search=ef_search,
            iterative_scan=self._settings.search_hnsw_iterative_scan,
        )

    async def keyword_candidates(
        self,
        org_id: UUID,
        user_id: UUID,
        *,
        query: str,
        filters: RetrievalFilters,
        top_k: int,
    ) -> list[ChunkCandidate]:
        return await self._chunks.search_keyword(
            org_id,
            user_id,
            query=query,
            language=self._settings.search_fts_language,
            filters=filters,
            top_k=top_k,
        )

    async def candidates_by_ids(
        self, org_id: UUID, user_id: UUID, chunk_ids: list[UUID]
    ) -> list[ChunkCandidate]:
        """ACL-filtered fetch of specific chunk ids (no leak by direct id)."""
        return await self._chunks.candidates_by_ids(org_id, user_id, chunk_ids)
