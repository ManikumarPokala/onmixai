"""Hybrid-search hot-path benchmark (performance.md §2 / Sprint 3 exit criterion 1).

Bulk-loads a corpus (BENCH_CHUNKS, default 100k) via the pgvector fast path (drop
the HNSW index, insert, rebuild once, ANALYZE) and asserts the hybrid /search p95
is under budget. Marked ``benchmark`` so it runs only in the benchmarks CI job, not
the default suite. This dim=8 number is a REGRESSION TRIPWIRE — it asserts the
production plan stays HNSW-backed and p95 < 3 s — NOT the production capacity figure.
The canonical capacity evidence is the 1536-dim sweep (scripts/drills/ef_search_sweep.py,
recorded in ADR 0009); the dim=8 benchmark trades realism for CI speed (ADR 0009).
"""

import os
import time
from typing import Any
from uuid import uuid4

import numpy as np
import pytest
from sqlalchemy import ClauseElement, Executable, insert, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.compiler import SQLCompiler

from src.identity.models import Organization, Role, User
from src.identity.schemas import AuthContext
from src.knowledge.models import Chunk, Collection, CollectionPermission, Document, DocumentStatus
from src.knowledge.repository import ChunkRepository
from src.knowledge.schemas import RetrievalFilters
from src.knowledge.service import ChunkRetrievalService
from src.search.schemas import SearchQuery
from src.search.service import SearchService
from src.shared.audit import AuditEmitter
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.fakes.fake_embedder import FakeEmbedder

_N = int(os.environ.get("BENCH_CHUNKS", "100000"))
_BUDGET_MS = float(os.environ.get("SEARCH_P95_BUDGET_MS", "3000"))
_QUERIES = 30
_HNSW = (
    "CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops) "
    "WITH (m = 16, ef_construction = 64)"
)


class _Explain(Executable, ClauseElement):
    """EXPLAIN <stmt> keeping bind params (pgvector vector doesn't render as a literal)."""

    inherit_cache = False

    def __init__(self, statement: Any) -> None:
        self.statement = statement


@compiles(_Explain, "postgresql")
def _compile_explain(element: _Explain, compiler: SQLCompiler, **kw: Any) -> str:
    return "EXPLAIN " + compiler.process(element.statement, **kw)


@pytest.mark.benchmark
async def test_hybrid_search_p95_under_budget(
    pg_container: dict[str, str], settings: Settings
) -> None:
    dim = settings.embedding_dimension
    rng = np.random.default_rng(99)
    owner = create_async_engine(pg_container["owner_url"])
    app = create_async_engine(pg_container["app_url"])
    om = async_sessionmaker(owner, expire_on_commit=False)
    org, user, coll, doc = uuid4(), uuid4(), uuid4(), uuid4()

    async with om() as s:
        await set_tenant_context(s, org)
        s.add(Organization(id=org, name="bench", slug=f"bench-{org}"))
        s.add(
            User(
                id=user,
                org_id=org,
                email=f"u@{org}.t",
                password_hash="x",
                full_name="U",
                role=Role.OWNER,
            )
        )
        await s.flush()
        s.add(Collection(id=coll, org_id=org, name="c", created_by=user))
        await s.flush()
        s.add(CollectionPermission(org_id=org, collection_id=coll, user_id=user, permission="read"))
        s.add(
            Document(
                id=doc,
                org_id=org,
                collection_id=coll,
                filename="f",
                content_type="text/plain",
                size_bytes=10,
                storage_key=f"k-{doc}",
                content_hash="h",
                status=DocumentStatus.READY,
                created_by=user,
            )
        )
        await s.commit()

    try:
        async with om() as s:
            await s.execute(text("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw"))
            await s.commit()
        async with om() as s:
            await set_tenant_context(s, org)
            for start in range(0, _N, 5000):
                size = min(5000, _N - start)
                vecs = rng.random((size, dim)).tolist()
                await s.execute(
                    insert(Chunk),
                    [
                        {
                            "id": uuid4(),
                            "org_id": org,
                            "document_id": doc,
                            "seq": start + i,
                            "content": f"benchmark chunk {start + i} term{(start + i) % 500}",
                            "content_hash": f"h{start + i}",
                            "token_count": 3,
                            "chunk_metadata": {},
                            "embedding": vecs[i],
                        }
                        for i in range(size)
                    ],
                )
            await s.commit()
        async with om() as s:
            # Serial build: a parallel HNSW build needs > a default container's
            # 64 MB /dev/shm. At the test dimension the corpus is small, so serial
            # is fast and needs no shm tuning in CI.
            await s.execute(text("SET statement_timeout = 0"))
            await s.execute(text("SET max_parallel_maintenance_workers = 0"))
            await s.execute(text(_HNSW))
            await s.execute(text("ANALYZE chunks"))
            await s.commit()

        embedder = FakeEmbedder(dim)
        actor = AuthContext(user_id=user, org_id=org, role=Role.OWNER)
        latencies: list[float] = []
        async with async_sessionmaker(app, expire_on_commit=False)() as s:
            await set_tenant_context(s, org)
            # The vector arm must actually USE the HNSW index at scale, not silently
            # fall back to an exact Seq-Scan-then-sort (which meets the budget at 100k
            # but is O(n) and breaks it at 1M). Assert the production plan, with the
            # same planner steer search_vector applies (ADR 0009), is index-backed.
            repo = ChunkRepository(s)
            await s.execute(
                text("SELECT set_config('hnsw.ef_search', :e, true)"),
                {"e": str(settings.search_ef_search)},
            )
            await s.execute(
                text("SELECT set_config('hnsw.iterative_scan', :i, true)"),
                {"i": settings.search_hnsw_iterative_scan},
            )
            await s.execute(text("SET LOCAL enable_sort = off"))
            probe_vec = (await embedder.embed(["probe"]))[0]
            vstmt = repo.vector_select(org, user, probe_vec, RetrievalFilters(), 10)
            plan = "\n".join((await s.execute(_Explain(vstmt))).scalars().all())
            await s.execute(text("SET LOCAL enable_sort = on"))
            assert "ix_chunks_embedding_hnsw" in plan, f"vector arm not HNSW-backed:\n{plan}"
            assert "Seq Scan on chunks" not in plan, f"vector arm fell back to seqscan:\n{plan}"

            service = SearchService(
                reader=ChunkRetrievalService(repo, settings),
                embedder=embedder,
                audit=AuditEmitter(),
                settings=settings,
            )
            for i in range(_QUERIES):
                started = time.monotonic()
                await service.search(actor, SearchQuery(query=f"term{i} benchmark", limit=10))
                latencies.append((time.monotonic() - started) * 1000)
        p95 = float(np.percentile(np.array(latencies), 95))
        print(f"\n[bench] /search p95={p95:.0f}ms ({_QUERIES}q, {_N} chunks, dim={dim})")
        assert p95 < _BUDGET_MS, f"p95 {p95:.0f}ms exceeds budget {_BUDGET_MS:.0f}ms"
    finally:
        async with om() as s:
            await s.execute(
                text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(org)}
            )
            await s.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": org})
            await s.commit()
        await owner.dispose()
        await app.dispose()
