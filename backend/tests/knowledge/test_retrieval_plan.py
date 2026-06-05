"""Plan-assertion tests (CLAUDE.md §7): the vector arm uses the HNSW index, the
keyword arm reaches chunks by index (never a sequential scan), and the GIN FTS
index exists on the tsvector column.

Per-arm planner methods are disabled so the intended access path is chosen
deterministically even on a small, unanalyzed test table: the vector arm disables
seqscan+sort (the only way to get distance order without a sort is the HNSW index
scan); the keyword arm disables seqscan (it must use an index). Whether the planner
prefers the GIN bitmap or the org-index bitmap+filter for the keyword arm is a
cost decision driven by corpus statistics (at 100k scale — Task 7 — GIN wins); the
invariant asserted here is that chunks are never sequential-scanned, and that the
GIN index is present to serve the FTS predicate.
"""

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import ClauseElement, Executable, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.compiler import SQLCompiler

from src.identity.models import Organization, Role, User
from src.knowledge.models import Chunk, Collection, CollectionPermission, Document, DocumentStatus
from src.knowledge.repository import ChunkRepository
from src.knowledge.schemas import RetrievalFilters
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.fakes.fake_embedder import FakeEmbedder


class _Explain(Executable, ClauseElement):
    """EXPLAIN <stmt> keeping bind params (pgvector vector / regconfig don't render
    as literals)."""

    inherit_cache = False

    def __init__(self, statement: Any) -> None:
        self.statement = statement


@compiles(_Explain, "postgresql")
def _compile_explain(element: _Explain, compiler: SQLCompiler, **kw: Any) -> str:
    return "EXPLAIN " + compiler.process(element.statement, **kw)


async def _seed(session: AsyncSession, embedder: FakeEmbedder) -> tuple[UUID, UUID]:
    org_id, user_id, collection_id, document_id = uuid4(), uuid4(), uuid4(), uuid4()
    await set_tenant_context(session, org_id)
    session.add(Organization(id=org_id, name="p", slug=f"p-{org_id}"))
    session.add(
        User(
            id=user_id,
            org_id=org_id,
            email=f"u@{org_id}.test",
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
            size_bytes=20,
            storage_key=f"org/{org_id}/doc/{document_id}",
            content_hash="h",
            status=DocumentStatus.READY,
            created_by=user_id,
        )
    )
    await session.flush()
    session.add(
        Chunk(
            id=uuid4(),
            org_id=org_id,
            document_id=document_id,
            seq=0,
            content="alpha beta retrieval plan content",
            content_hash="ch",
            token_count=5,
            chunk_metadata={"page": 1},
            embedding=embedder._vector("alpha beta"),
        )
    )
    await session.flush()
    return org_id, user_id


async def _plan(session: AsyncSession, statement: Any, *, disable: tuple[str, ...]) -> str:
    for method in disable:
        await session.execute(text(f"SET LOCAL {method} = off"))
    rows = (await session.execute(_Explain(statement))).scalars().all()
    return "\n".join(rows)


@pytest.fixture
async def repo_and_ids(
    db_session: AsyncSession, settings: Settings
) -> tuple[ChunkRepository, UUID, UUID, FakeEmbedder]:
    embedder = FakeEmbedder(settings.embedding_dimension)
    org_id, user_id = await _seed(db_session, embedder)
    return ChunkRepository(db_session), org_id, user_id, embedder


async def test_vector_arm_uses_hnsw_index_not_seqscan(
    repo_and_ids: tuple[ChunkRepository, UUID, UUID, FakeEmbedder], db_session: AsyncSession
) -> None:
    repo, org_id, user_id, embedder = repo_and_ids
    stmt = repo.vector_select(
        org_id, user_id, embedder._vector("alpha beta"), RetrievalFilters(), 60
    )
    # seqscan + sort off → the only distance-ordered path left is the HNSW index scan.
    plan = await _plan(db_session, stmt, disable=("enable_seqscan", "enable_sort"))
    assert "ix_chunks_embedding_hnsw" in plan
    assert "Seq Scan on chunks" not in plan


async def test_keyword_arm_reaches_chunks_by_index(
    repo_and_ids: tuple[ChunkRepository, UUID, UUID, FakeEmbedder], db_session: AsyncSession
) -> None:
    repo, org_id, user_id, _ = repo_and_ids
    stmt = repo.keyword_select(org_id, user_id, "alpha beta", "english", RetrievalFilters(), 60)
    plan = await _plan(db_session, stmt, disable=("enable_seqscan",))
    assert "Seq Scan on chunks" not in plan  # index-backed, never a sequential scan


async def test_gin_fulltext_index_exists_on_content_tsv(db_session: AsyncSession) -> None:
    # The GIN index that serves the keyword arm's @@ predicate is present and built
    # on the tsvector column (it wins on cost at corpus scale — Task 7).
    indexdef = (
        await db_session.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'chunks' AND indexname = 'ix_chunks_content_tsv_gin'"
            )
        )
    ).scalar_one()
    assert "USING gin" in indexdef and "content_tsv" in indexdef
