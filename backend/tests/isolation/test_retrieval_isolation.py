"""Isolation suite — the retrieval path. Org A's actor retrieves zero of org B's
chunks through every arm (vector, keyword, by-id), as the non-bypassrls runtime
role so both application scoping and RLS are exercised."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.models import Organization, Role, User
from src.knowledge.models import Chunk, Collection, CollectionPermission, Document, DocumentStatus
from src.knowledge.repository import ChunkRepository
from src.knowledge.schemas import RetrievalFilters
from src.knowledge.service import ChunkRetrievalService
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.fakes.fake_embedder import FakeEmbedder


@dataclass(frozen=True)
class _Org:
    org_id: UUID
    user_id: UUID
    chunk_id: UUID


async def _seed(session: AsyncSession, embedder: FakeEmbedder, label: str) -> _Org:
    org_id, user_id, collection_id, document_id, chunk_id = (uuid4() for _ in range(5))
    await set_tenant_context(session, org_id)
    session.add(Organization(id=org_id, name=label, slug=f"{label}-{org_id}"))
    session.add(
        User(
            id=user_id,
            org_id=org_id,
            email=f"u@{label}-{org_id}.test",
            password_hash="x",
            full_name="U",
            role=Role.OWNER,
        )
    )
    await session.flush()
    session.add(Collection(id=collection_id, org_id=org_id, name="c", created_by=user_id))
    await session.flush()
    session.add(
        CollectionPermission(
            org_id=org_id, collection_id=collection_id, user_id=user_id, permission="read"
        )
    )
    session.add(
        Document(
            id=document_id,
            org_id=org_id,
            collection_id=collection_id,
            filename="f.txt",
            content_type="text/plain",
            size_bytes=30,
            storage_key=f"org/{org_id}/doc/{document_id}",
            content_hash=f"{document_id}-h",
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
            content="shared retrieval keyword alpha",
            content_hash=f"{chunk_id}-h",
            token_count=4,
            chunk_metadata={"page": 1},
            embedding=embedder._vector("shared retrieval keyword alpha"),
        )
    )
    await session.flush()
    return _Org(org_id, user_id, chunk_id)


@pytest.fixture
async def two_orgs(
    db_session: AsyncSession, settings: Settings
) -> AsyncIterator[tuple[ChunkRetrievalService, _Org, _Org, FakeEmbedder]]:
    embedder = FakeEmbedder(settings.embedding_dimension)
    org_a = await _seed(db_session, embedder, "reta")
    org_b = await _seed(db_session, embedder, "retb")
    yield ChunkRetrievalService(ChunkRepository(db_session), settings), org_a, org_b, embedder


async def test_vector_arm_never_returns_other_orgs_chunks(
    two_orgs: tuple[ChunkRetrievalService, _Org, _Org, FakeEmbedder], db_session: AsyncSession
) -> None:
    service, org_a, org_b, embedder = two_orgs
    await set_tenant_context(db_session, org_a.org_id)
    got = await service.vector_candidates(
        org_a.org_id,
        org_a.user_id,
        embedding=embedder._vector("shared retrieval"),
        filters=RetrievalFilters(),
        top_k=100,
        ef_search=40,
    )
    ids = {c.chunk_id for c in got}
    assert org_a.chunk_id in ids and org_b.chunk_id not in ids


async def test_keyword_arm_never_returns_other_orgs_chunks(
    two_orgs: tuple[ChunkRetrievalService, _Org, _Org, FakeEmbedder], db_session: AsyncSession
) -> None:
    service, org_a, org_b, _ = two_orgs
    await set_tenant_context(db_session, org_a.org_id)
    got = await service.keyword_candidates(
        org_a.org_id, org_a.user_id, query="shared keyword", filters=RetrievalFilters(), top_k=100
    )
    ids = {c.chunk_id for c in got}
    assert org_a.chunk_id in ids and org_b.chunk_id not in ids


async def test_by_id_never_returns_other_orgs_chunks(
    two_orgs: tuple[ChunkRetrievalService, _Org, _Org, FakeEmbedder], db_session: AsyncSession
) -> None:
    service, org_a, org_b, _ = two_orgs
    await set_tenant_context(db_session, org_a.org_id)
    got = await service.candidates_by_ids(
        org_a.org_id, org_a.user_id, [org_a.chunk_id, org_b.chunk_id]
    )
    ids = {c.chunk_id for c in got}
    assert org_a.chunk_id in ids and org_b.chunk_id not in ids
