"""Hybrid-search hot-path benchmark (performance.md §2 / Sprint 3 exit criterion 1).

Bulk-loads a corpus (BENCH_CHUNKS, default 100k) via the pgvector fast path (drop
the HNSW index, insert, rebuild once, ANALYZE) and asserts the hybrid /search p95
is under budget. Marked ``benchmark`` so it runs only in the benchmarks CI job, not
the default suite. The realistic-dimension (1536) p95 + ef_search recall table are
measured offline by scripts/drills/ef_search_sweep.py and recorded in ADR 0009; this
gate guards the end-to-end query path + budget at the test embedding dimension.
"""

import os
import time
from uuid import uuid4

import numpy as np
import pytest
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.identity.models import Organization, Role, User
from src.identity.schemas import AuthContext
from src.knowledge.models import Chunk, Collection, CollectionPermission, Document, DocumentStatus
from src.knowledge.repository import ChunkRepository
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
            service = SearchService(
                reader=ChunkRetrievalService(ChunkRepository(s), settings),
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
