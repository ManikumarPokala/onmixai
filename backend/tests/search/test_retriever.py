"""Permission-aware retriever: both arms ACL-filter in the predicate before
ranking — a chunk in a collection the actor cannot access is never a candidate,
and another org's chunks are never visible. Real Postgres; fake embedder."""

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.identity.models import Organization, Role, User
from src.knowledge.models import Chunk, Collection, CollectionPermission, Document, DocumentStatus
from src.knowledge.repository import ChunkRepository
from src.knowledge.schemas import RetrievalFilters
from src.knowledge.service import ChunkRetrievalService
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.fakes.fake_embedder import FakeEmbedder


@dataclass(frozen=True)
class _Seeded:
    org_id: UUID
    user_id: UUID
    accessible_chunk: UUID  # in a collection the user can READ
    forbidden_chunk: UUID  # in a collection the user has no permission on


async def _add_doc_with_chunk(
    session: AsyncSession,
    *,
    org_id: UUID,
    user_id: UUID,
    collection_id: UUID,
    content: str,
    embedder: FakeEmbedder,
) -> UUID:
    document_id, chunk_id = uuid4(), uuid4()
    session.add(
        Document(
            id=document_id,
            org_id=org_id,
            collection_id=collection_id,
            filename="f.txt",
            content_type="text/plain",
            size_bytes=len(content),
            storage_key=f"org/{org_id}/doc/{document_id}",
            content_hash=f"{document_id}-hash",
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
            content=content,
            content_hash=f"{chunk_id}-hash",
            token_count=len(content.split()),
            chunk_metadata={"page": 1},
            embedding=embedder._vector(content),
        )
    )
    await session.flush()
    return chunk_id


async def _seed(session: AsyncSession, embedder: FakeEmbedder, *, label: str) -> _Seeded:
    org_id, user_id = uuid4(), uuid4()
    readable_id, forbidden_id = uuid4(), uuid4()
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
    session.add(Collection(id=readable_id, org_id=org_id, name="readable", created_by=user_id))
    session.add(Collection(id=forbidden_id, org_id=org_id, name="forbidden", created_by=user_id))
    await session.flush()
    # The user has READ on the readable collection only — none on "forbidden".
    session.add(
        CollectionPermission(
            org_id=org_id, collection_id=readable_id, user_id=user_id, permission="read"
        )
    )
    accessible = await _add_doc_with_chunk(
        session,
        org_id=org_id,
        user_id=user_id,
        collection_id=readable_id,
        content="alpha alpha shared apple",
        embedder=embedder,
    )
    forbidden = await _add_doc_with_chunk(
        session,
        org_id=org_id,
        user_id=user_id,
        collection_id=forbidden_id,
        content="alpha alpha shared banana",
        embedder=embedder,
    )
    return _Seeded(org_id, user_id, accessible, forbidden)


@pytest.fixture
async def retriever(
    db_session: AsyncSession, settings: Settings
) -> tuple[ChunkRetrievalService, _Seeded, FakeEmbedder]:
    embedder = FakeEmbedder(settings.embedding_dimension)
    seeded = await _seed(db_session, embedder, label="orga")
    service = ChunkRetrievalService(ChunkRepository(db_session), settings)
    return service, seeded, embedder


async def test_vector_arm_excludes_inaccessible_collection(
    retriever: tuple[ChunkRetrievalService, _Seeded, FakeEmbedder],
) -> None:
    service, seeded, embedder = retriever
    candidates = await service.vector_candidates(
        seeded.org_id,
        seeded.user_id,
        embedding=embedder._vector("alpha shared"),
        filters=RetrievalFilters(),
        top_k=50,
        ef_search=40,
    )
    ids = {c.chunk_id for c in candidates}
    assert seeded.accessible_chunk in ids  # readable collection surfaces
    assert seeded.forbidden_chunk not in ids  # no-permission collection never a candidate


async def test_keyword_arm_excludes_inaccessible_collection(
    retriever: tuple[ChunkRetrievalService, _Seeded, FakeEmbedder],
) -> None:
    service, seeded, _ = retriever
    candidates = await service.keyword_candidates(
        seeded.org_id, seeded.user_id, query="alpha shared", filters=RetrievalFilters(), top_k=50
    )
    ids = {c.chunk_id for c in candidates}
    assert seeded.accessible_chunk in ids
    assert seeded.forbidden_chunk not in ids


async def test_no_permissions_yields_no_candidates(
    db_session: AsyncSession, settings: Settings
) -> None:
    # A second org with its own data; this actor has no permissions at all.
    embedder = FakeEmbedder(settings.embedding_dimension)
    other = await _seed(db_session, embedder, label="orgb")
    service = ChunkRetrievalService(ChunkRepository(db_session), settings)
    await set_tenant_context(db_session, other.org_id)
    # Use a user id with zero permission rows in this org.
    stranger = uuid4()
    vector = await service.vector_candidates(
        other.org_id,
        stranger,
        embedding=embedder._vector("alpha"),
        filters=RetrievalFilters(),
        top_k=50,
        ef_search=40,
    )
    keyword = await service.keyword_candidates(
        other.org_id, stranger, query="alpha", filters=RetrievalFilters(), top_k=50
    )
    assert vector == [] and keyword == []


async def test_cross_org_chunks_never_candidates(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    # Org A's actor must never retrieve org B's chunks (committed data; RLS + predicate).
    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    embedder = FakeEmbedder(settings.embedding_dimension)
    async with maker() as session:
        org_a = await _seed(session, embedder, label="xa")
        await session.commit()
    async with maker() as session:
        org_b = await _seed(session, embedder, label="xb")
        await session.commit()

    async with maker() as session:
        await set_tenant_context(session, org_a.org_id)
        service = ChunkRetrievalService(ChunkRepository(session), settings)
        vector = await service.vector_candidates(
            org_a.org_id,
            org_a.user_id,
            embedding=embedder._vector("alpha shared"),
            filters=RetrievalFilters(),
            top_k=100,
            ef_search=40,
        )
    returned = {c.chunk_id for c in vector}
    assert org_b.accessible_chunk not in returned and org_b.forbidden_chunk not in returned
