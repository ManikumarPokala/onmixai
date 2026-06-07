#!/usr/bin/env python
"""HNSW ef_search tuning sweep + Phase-2 retrieval evidence (Task 7 / ADR 0009).

Bulk-loads a SEED_CHUNKS (default 100k) embedded corpus via the pgvector fast path
(drop the HNSW index, insert, rebuild once SERIALLY — a parallel build needs more
than a default container's /dev/shm — then ANALYZE) and reports, in one pass:

  * ANALYZE confirmation (post-seed stats are fresh, not stale — a stale planner on
    a fresh 100k load can fake either an index choice or a recall number).
  * EXPLAIN (FORMAT JSON) node excerpts for the vector arm (HNSW) and keyword arm,
    plus a cross-org candidate proof.
  * ef_search x {recall@5 vs exact KNN, p50, p95, p99} tuning table.
  * Arm-level latency breakdown (vector / keyword / fusion) at the chosen default.

Run from the repo root against a dev Postgres (dim must match EMBEDDING_DIMENSION):
    EMBEDDING_DIMENSION=1536 backend/.venv/bin/python scripts/drills/ef_search_sweep.py
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any
from uuid import UUID, uuid4

import numpy as np
from sqlalchemy import ClauseElement, Executable, insert, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.compiler import SQLCompiler

from src.identity.models import Organization, Role, User
from src.knowledge.models import Chunk, Collection, CollectionPermission, Document, DocumentStatus
from src.knowledge.repository import ChunkRepository
from src.knowledge.schemas import RetrievalFilters
from src.search.rules import rrf_fuse
from src.shared.database import set_tenant_context

OWNER_URL = os.environ.get(
    "MIGRATION_DATABASE_URL", "postgresql+asyncpg://onmixai:onmixai@localhost:5440/onmixai"
)
DIM = int(os.environ.get("EMBEDDING_DIMENSION", "1536"))
SEED = int(os.environ.get("SEED_CHUNKS", "100000"))
QUERIES = 40
K = 5
RRF_K = 60
EF_GRID = [10, 20, 40, 80, 120, 200]
_FILTERS = RetrievalFilters()
_HNSW = (
    "CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops) "
    "WITH (m = 16, ef_construction = 64)"
)


class _ExplainJSON(Executable, ClauseElement):
    """EXPLAIN (FORMAT JSON) <stmt> keeping bind params (vector/regconfig don't render)."""

    inherit_cache = False

    def __init__(self, statement: Any) -> None:
        self.statement = statement


@compiles(_ExplainJSON, "postgresql")
def _compile(element: _ExplainJSON, compiler: SQLCompiler, **kw: Any) -> str:
    return "EXPLAIN (FORMAT JSON) " + compiler.process(element.statement, **kw)


class _ExplainAB(Executable, ClauseElement):
    """EXPLAIN (ANALYZE, BUFFERS) <stmt> in TEXT — actually executes the query so the
    HNSW node reports real shared-buffer access + server-side time per ef_search."""

    inherit_cache = False

    def __init__(self, statement: Any) -> None:
        self.statement = statement


@compiles(_ExplainAB, "postgresql")
def _compile_ab(element: _ExplainAB, compiler: SQLCompiler, **kw: Any) -> str:
    return "EXPLAIN (ANALYZE, BUFFERS) " + compiler.process(element.statement, **kw)


def _hnsw_lines(rows: list[str]) -> list[str]:
    """The HNSW index-scan node line, its following Buffers: line, and Execution Time.
    ef_search widens the layer-0 beam, so a larger ef must touch more index pages (a
    higher 'Buffers: shared hit=' on that node) and cost more server-side time —
    identical counts across a 20x ef range would mean the GUC never reached the probe."""
    out: list[str] = []
    for i, line in enumerate(rows):
        s = line.strip()
        if "ix_chunks_embedding_hnsw" in s:
            out.append(s)
            if i + 1 < len(rows) and "Buffers:" in rows[i + 1]:
                out.append(rows[i + 1].strip())
        elif s.startswith("Execution Time:"):
            out.append(s)
    return out


def _pct(values: list[float], p: float) -> float:
    return float(np.percentile(np.array(values), p))


def _index_nodes(plan: Any) -> str:
    """Flatten a FORMAT JSON plan (already parsed) to 'NodeType[ on rel][ using index]'."""
    out: list[str] = []

    def walk(node: dict[str, Any]) -> None:
        line = node.get("Node Type", "?")
        if "Relation Name" in node:
            line += f" on {node['Relation Name']}"
        if "Index Name" in node:
            line += f" using {node['Index Name']}"
        out.append(line)
        for child in node.get("Plans", []):
            walk(child)

    walk(plan[0]["Plan"])
    return " -> ".join(out)


async def _seed_identity(maker: Any, org: UUID, user: UUID, coll: UUID, doc: UUID, label: str) -> None:
    async with maker() as s:
        await set_tenant_context(s, org)
        s.add(Organization(id=org, name=label, slug=f"{label}-{org}"))
        s.add(User(id=user, org_id=org, email=f"u@{org}.t", password_hash="x", full_name="U",
                   role=Role.OWNER))
        await s.flush()
        s.add(Collection(id=coll, org_id=org, name="c", created_by=user))
        await s.flush()
        s.add(CollectionPermission(org_id=org, collection_id=coll, user_id=user, permission="read"))
        s.add(Document(id=doc, org_id=org, collection_id=coll, filename="f", content_type="text/plain",
                       size_bytes=10, storage_key=f"k-{doc}", content_hash=f"h-{doc}",
                       status=DocumentStatus.READY, created_by=user))
        await s.flush()
        await s.commit()


async def main() -> int:  # noqa: C901 - linear measurement script
    engine = create_async_engine(OWNER_URL, connect_args={"command_timeout": 3600})
    maker = async_sessionmaker(engine, expire_on_commit=False)
    rng = np.random.default_rng(1234)
    org_a, user_a, coll_a, doc_a = uuid4(), uuid4(), uuid4(), uuid4()
    org_b, user_b, coll_b, doc_b = uuid4(), uuid4(), uuid4(), uuid4()
    org_b_chunk = uuid4()

    await _seed_identity(maker, org_a, user_a, coll_a, doc_a, "bencha")
    await _seed_identity(maker, org_b, user_b, coll_b, doc_b, "benchb")

    print(f"[load] dropping HNSW index; bulk-loading {SEED} dim-{DIM} chunks into org A ...")
    t0 = time.monotonic()
    async with maker() as s:
        await s.execute(text("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw"))
        await s.commit()
    async with maker() as s:
        await set_tenant_context(s, org_a)
        for start in range(0, SEED, 5000):
            size = min(5000, SEED - start)
            vecs = rng.random((size, DIM)).tolist()
            await s.execute(
                insert(Chunk),
                [
                    {
                        "id": uuid4(), "org_id": org_a, "document_id": doc_a, "seq": start + i,
                        "content": f"benchmark chunk {start + i} term{(start + i) % 500}",
                        "content_hash": f"a{start + i}", "token_count": 3, "chunk_metadata": {},
                        "embedding": vecs[i],
                    }
                    for i in range(size)
                ],
            )
        await s.commit()
    async with maker() as s:
        await set_tenant_context(s, org_b)
        await s.execute(
            insert(Chunk),
            [{
                "id": org_b_chunk, "org_id": org_b, "document_id": doc_b, "seq": 0,
                "content": "orgbsecret confidential term7 sensitive material",
                "content_hash": "b0", "token_count": 5, "chunk_metadata": {},
                "embedding": rng.random(DIM).tolist(),
            }],
        )
        await s.commit()
    print(f"[load] inserted in {time.monotonic() - t0:.0f}s; building HNSW index ...")

    # The parallel HNSW build uses dynamic shared memory; a default container's
    # 64 MB /dev/shm is too small at 1536-dim — run Postgres with shm_size >= 512 MB
    # (infra/docker-compose.yml sets this), or set max_parallel_maintenance_workers=0
    # to build serially.
    t1 = time.monotonic()
    async with maker() as s:
        await s.execute(text("SET statement_timeout = 0"))
        await s.execute(text("SET maintenance_work_mem = '256MB'"))
        await s.execute(text(_HNSW))
        await s.execute(text("ANALYZE chunks"))
        await s.commit()
    print(f"[load] index built + ANALYZE in {time.monotonic() - t1:.0f}s")

    async with maker() as s:
        live, last_analyze = (
            await s.execute(
                text("SELECT n_live_tup, last_analyze FROM pg_stat_user_tables WHERE relname='chunks'")
            )
        ).one()
    print(f"\n[analyze] chunks n_live_tup={live} last_analyze={last_analyze}")

    async def _plan(kind: str, *, ef: int | None = None, disable: tuple[str, ...] = ()) -> str:
        async with maker() as s:
            await set_tenant_context(s, org_a)
            repo = ChunkRepository(s)
            stmt = (
                repo.vector_select(org_a, user_a, rng.random(DIM).tolist(), _FILTERS, K)
                if kind == "vector"
                else repo.keyword_select(org_a, user_a, "term7", "english", _FILTERS, K)
            )
            if ef is not None:
                await s.execute(text("SELECT set_config('hnsw.ef_search', :e, true)"), {"e": str(ef)})
            for method in disable:
                await s.execute(text(f"SET LOCAL {method} = off"))
            plan = (await s.execute(_ExplainJSON(stmt))).scalar_one()
        return _index_nodes(plan)

    print("\n[explain] vector arm:", await _plan("vector", ef=40, disable=("enable_seqscan", "enable_sort")))
    print("[explain] keyword arm:", await _plan("keyword", disable=("enable_seqscan",)))

    # GUC-reaches-the-query proof: one fixed query vector, ef=10 vs ef=200, in the
    # SAME transaction as the set_config. SHOW must echo the value (else SET LOCAL
    # detached from the query's transaction); the HNSW node's shared-buffer counts
    # must rise with ef (else the GUC reached the session but not the index probe).
    probe_q = rng.random(DIM).tolist()
    print("\n[guc] ef_search reaches the query (SHOW + HNSW node buffers/time, same txn):")
    for ef in (10, 200):
        async with maker() as s:
            await set_tenant_context(s, org_a)
            repo = ChunkRepository(s)
            # Same planner steer the production search_vector applies (ADR 0009): the
            # ACL'd query only uses the HNSW index with enable_sort=off, else the planner
            # falls back to an exact Seq-Scan+sort and ef_search becomes inert.
            await s.execute(text("SELECT set_config('hnsw.ef_search', :e, true)"), {"e": str(ef)})
            await s.execute(text("SELECT set_config('hnsw.iterative_scan', 'strict_order', true)"))
            await s.execute(text("SET LOCAL enable_sort = off"))
            shown = (await s.execute(text("SHOW hnsw.ef_search"))).scalar_one()
            stmt = repo.vector_select(org_a, user_a, probe_q, _FILTERS, K)
            rows = (await s.execute(_ExplainAB(stmt))).scalars().all()
        print(f"  ef={ef:>3}  SHOW hnsw.ef_search={shown}")
        for line in _hnsw_lines(list(rows)):
            print(f"      {line}")

    async with maker() as s:
        await set_tenant_context(s, org_a)
        leaked = await ChunkRepository(s).search_keyword(
            org_a, user_a, query="orgbsecret", language="english", filters=_FILTERS, top_k=50
        )
    present = org_b_chunk in {c.chunk_id for c in leaked}
    print(f"[cross-org] org A keyword 'orgbsecret' -> {len(leaked)} hits; org B chunk present: {present}")

    queries = [rng.random(DIM).tolist() for _ in range(QUERIES)]
    exact: list[set] = []
    async with maker() as s:
        await set_tenant_context(s, org_a)
        repo = ChunkRepository(s)
        await s.execute(text("SET LOCAL enable_indexscan = off"))
        for q in queries:
            rows = (await s.execute(repo.vector_select(org_a, user_a, q, _FILTERS, K))).all()
            exact.append({r.chunk_id for r in rows})

    print(f"\n{'ef_search':>9} | {'recall@5':>8} | {'p50 ms':>7} | {'p95 ms':>7} | {'p99 ms':>7}")
    print("-" * 52)
    table: list[tuple[int, float, float, float, float]] = []
    for ef in EF_GRID:
        lat: list[float] = []
        rec: list[float] = []
        async with maker() as s:
            await set_tenant_context(s, org_a)
            repo = ChunkRepository(s)
            # Production planner steer (ADR 0009) so the table times the real HNSW path,
            # not the exact Seq-Scan+sort the planner picks for the ACL'd query otherwise.
            await s.execute(text("SELECT set_config('hnsw.iterative_scan', 'strict_order', true)"))
            await s.execute(text("SET LOCAL enable_sort = off"))
            for qi, q in enumerate(queries):
                await s.execute(text("SELECT set_config('hnsw.ef_search', :e, true)"), {"e": str(ef)})
                t = time.monotonic()
                rows = (await s.execute(repo.vector_select(org_a, user_a, q, _FILTERS, K))).all()
                lat.append((time.monotonic() - t) * 1000)
                rec.append(len({r.chunk_id for r in rows} & exact[qi]) / max(1, len(exact[qi])))
        table.append((ef, float(np.mean(rec)), _pct(lat, 50), _pct(lat, 95), _pct(lat, 99)))
        ef_, r5, p50, p95, p99 = table[-1]
        print(f"{ef_:>9} | {r5:>8.3f} | {p50:>7.1f} | {p95:>7.1f} | {p99:>7.1f}")

    # NB: recall@5 on this corpus is an ARTIFACT, not a quality signal — uniform-random
    # 1536-dim vectors have no neighbourhood structure (distance concentration), so HNSW
    # returns an equally-valid but low-overlap top-k vs the exact baseline. The sweep
    # bounds LATENCY (a non-constraint here); ef_search is set generously for recall
    # headroom on real embeddings and must be tuned on a labelled set (ADR 0009).
    fastest = min(table, key=lambda r: r[3])
    print(f"\n[latency] every ef clears the budget; max p95={max(r[3] for r in table):.1f}ms, "
          f"fastest ef={fastest[0]} @ p95={fastest[3]:.1f}ms. recall@5 is uninformative on "
          f"synthetic data (see note above).")

    arm_ef = EF_GRID[-1]  # the production default (search_ef_search); recall-safest end
    vt: list[float] = []
    kt: list[float] = []
    ft: list[float] = []
    async with maker() as s:
        await set_tenant_context(s, org_a)
        repo = ChunkRepository(s)
        for qi, q in enumerate(queries):
            t = time.monotonic()
            v = await repo.search_vector(org_a, user_a, embedding=q, filters=_FILTERS, top_k=K, ef_search=arm_ef, iterative_scan="strict_order")
            vt.append((time.monotonic() - t) * 1000)
            t = time.monotonic()
            kw = await repo.search_keyword(org_a, user_a, query=f"term{qi % 500}", language="english", filters=_FILTERS, top_k=K)
            kt.append((time.monotonic() - t) * 1000)
            t = time.monotonic()
            rrf_fuse([v, kw], k=RRF_K)
            ft.append((time.monotonic() - t) * 1000)
    print(f"[arms @ ef={arm_ef}] vector p50={_pct(vt, 50):.1f}ms  "
          f"keyword p50={_pct(kt, 50):.1f}ms  fusion p50={_pct(ft, 50):.2f}ms")

    async with maker() as s:
        for o in (org_a, org_b):
            await s.execute(text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(o)})
            await s.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": o})
        await s.commit()
    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
