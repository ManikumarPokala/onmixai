"""Knowledge tenant-isolation suite (permanently blocking).

Runs as the non-superuser, non-bypassrls runtime role, so application org-scoping
AND Postgres RLS are both exercised across all five knowledge tenant tables
(collections, collection_permissions, documents, chunks, storage_deletion_outbox).
Proves org A can never reach org B's rows by direct id (IDOR), by collection-
permission probing, by a raw unfiltered count, or through the worker's repository
methods running under org A's tenant context.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.models import Organization, Role, User
from src.knowledge.models import (
    Chunk,
    Collection,
    CollectionPermission,
    Document,
    DocumentStatus,
    Permission,
    StorageDeletionOutbox,
)
from src.knowledge.repository import (
    ChunkRepository,
    CollectionRepository,
    DocumentRepository,
)
from src.shared.database import set_tenant_context

_TENANT_TABLES = (
    "collections",
    "collection_permissions",
    "documents",
    "chunks",
    "storage_deletion_outbox",
)


@dataclass(frozen=True)
class _Org:
    org_id: UUID
    user_id: UUID
    collection_id: UUID
    document_id: UUID
    chunk_id: UUID


async def _seed_org(session: AsyncSession, label: str) -> _Org:
    """Insert one org's full knowledge graph under its own tenant context.

    Flushes level by level (org/user → collection → document → chunk) so each FK
    parent exists before its child — FK checks run under FORCE RLS as the runtime
    role, so the parent must be visible in this org's context when the child inserts.
    """
    org_id, user_id, collection_id, document_id, chunk_id = (uuid4() for _ in range(5))
    await set_tenant_context(session, org_id)
    session.add(Organization(id=org_id, name=label, slug=f"{label}-{org_id}"))
    session.add(
        User(
            id=user_id,
            org_id=org_id,
            email=f"owner@{label}-{org_id}.test",
            password_hash="x",
            full_name="Owner",
            role=Role.OWNER,
        )
    )
    await session.flush()
    session.add(Collection(id=collection_id, org_id=org_id, name="C", created_by=user_id))
    await session.flush()
    session.add(
        CollectionPermission(
            org_id=org_id,
            collection_id=collection_id,
            user_id=user_id,
            permission=Permission.MANAGE,
        )
    )
    session.add(
        Document(
            id=document_id,
            org_id=org_id,
            collection_id=collection_id,
            filename="f.txt",
            content_type="text/plain",
            size_bytes=10,
            storage_key=f"org/{org_id}/doc/{document_id}",
            content_hash="0" * 64,
            status=DocumentStatus.READY,
            created_by=user_id,
        )
    )
    await session.flush()
    session.add(
        Chunk(
            id=chunk_id,
            org_id=org_id,
            document_id=document_id,
            seq=0,
            content="chunk",
            content_hash=f"{label}-hash",
            token_count=1,
            chunk_metadata={},
        )
    )
    session.add(StorageDeletionOutbox(org_id=org_id, storage_key=f"org/{org_id}/orphan"))
    await session.flush()
    return _Org(org_id, user_id, collection_id, document_id, chunk_id)


@pytest.fixture
async def two_orgs(db_session: AsyncSession) -> AsyncIterator[tuple[_Org, _Org]]:
    org_a = await _seed_org(db_session, "orga")
    org_b = await _seed_org(db_session, "orgb")
    yield org_a, org_b


async def test_cannot_read_other_orgs_document_by_id(
    two_orgs: tuple[_Org, _Org], db_session: AsyncSession
) -> None:
    org_a, org_b = two_orgs
    await set_tenant_context(db_session, org_a.org_id)
    documents = DocumentRepository(db_session)

    assert await documents.get(org_a.org_id, org_b.document_id) is None  # IDOR by document id
    assert await documents.get(org_a.org_id, org_a.document_id) is not None  # own row visible


async def test_cannot_read_other_orgs_chunks(
    two_orgs: tuple[_Org, _Org], db_session: AsyncSession
) -> None:
    org_a, org_b = two_orgs
    await set_tenant_context(db_session, org_a.org_id)
    chunks = ChunkRepository(db_session)

    # Org B's document id resolves to zero chunks under org A's context (IDOR).
    assert await chunks.count_for_document(org_a.org_id, org_b.document_id) == 0
    assert await chunks.hashes_for_document(org_a.org_id, org_b.document_id) == set()
    assert await chunks.count_for_document(org_a.org_id, org_a.document_id) == 1  # own visible


async def test_cannot_probe_other_orgs_collection_or_permissions(
    two_orgs: tuple[_Org, _Org], db_session: AsyncSession
) -> None:
    org_a, org_b = two_orgs
    await set_tenant_context(db_session, org_a.org_id)
    collections = CollectionRepository(db_session)

    assert await collections.get(org_a.org_id, org_b.collection_id) is None  # IDOR
    # Permission-bypass probe: org A cannot see org B's grant even naming B's ids.
    assert (
        await collections.get_permission(org_a.org_id, org_b.collection_id, org_b.user_id) is None
    )
    # Own permission still resolves.
    assert (
        await collections.get_permission(org_a.org_id, org_a.collection_id, org_a.user_id)
        is not None
    )


@pytest.mark.parametrize("table", _TENANT_TABLES)
async def test_raw_count_respects_rls(
    two_orgs: tuple[_Org, _Org], db_session: AsyncSession, table: str
) -> None:
    org_a, org_b = two_orgs
    # Each org sees exactly its own single row in every tenant table, even with no
    # WHERE clause — RLS, not application scoping, enforces this. `table` is from a
    # fixed allow-list, not user input.
    await set_tenant_context(db_session, org_a.org_id)
    count_a = (await db_session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
    assert count_a == 1

    await set_tenant_context(db_session, org_b.org_id)
    count_b = (await db_session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
    assert count_b == 1


async def test_worker_context_cannot_touch_other_orgs_rows(
    two_orgs: tuple[_Org, _Org], db_session: AsyncSession
) -> None:
    org_a, org_b = two_orgs
    # Simulate an org-A worker running its repository methods against org B's ids.
    await set_tenant_context(db_session, org_a.org_id)
    documents = DocumentRepository(db_session)
    chunks = ChunkRepository(db_session)

    won = await documents.claim_for_processing(org_a.org_id, org_b.document_id, datetime.now(UTC))
    assert won is False  # cannot claim another org's document
    await chunks.delete_for_document(org_a.org_id, org_b.document_id)  # affects 0 rows under RLS

    # Org B's rows are untouched (verified under org B's own context).
    await set_tenant_context(db_session, org_b.org_id)
    assert await chunks.count_for_document(org_b.org_id, org_b.document_id) == 1
    document_b = await DocumentRepository(db_session).get(org_b.org_id, org_b.document_id)
    assert document_b is not None and document_b.status == DocumentStatus.READY  # never claimed
