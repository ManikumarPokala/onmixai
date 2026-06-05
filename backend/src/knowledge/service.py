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
    UploadTooLargeError,
)
from src.knowledge.models import Collection, Document, DocumentStatus, Permission
from src.knowledge.repository import CollectionRepository, DocumentRepository
from src.knowledge.rules import (
    ensure_collection_permission,
    ensure_upload_acceptable,
    ensure_within_quota,
)
from src.knowledge.schemas import CollectionDTO, DocumentDTO, UploadAccepted
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
        storage: ObjectStorage,
        queue: JobQueue,
        audit: AuditEmitter,
        quota_reader: OrgQuotaReader,
        settings: Settings,
    ) -> None:
        self._session = session
        self._collections = collections
        self._documents = documents
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
                status=DocumentStatus.QUEUED,
                created_by=actor.user_id,
            )
        )
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="document.uploaded",
            resource_id=document.id,
        )
        document_id, org_id = document.id, actor.org_id
        register_after_commit(
            self._session,
            lambda: self._queue.enqueue_ingest(document_id=document_id, org_id=org_id),
        )
        return UploadAccepted(document_id=document.id, status=document.status)

    async def get_document(self, actor: AuthContext, document_id: UUID) -> DocumentDTO:
        document = await self._documents.get(actor.org_id, document_id)
        if document is None:
            raise DocumentNotFoundError()
        await self._require_permission(actor, document.collection_id, Permission.READ)
        return DocumentDTO.from_model(document)

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
