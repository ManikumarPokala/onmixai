# Runbook — Re-index embeddings / tune retrieval

Covers three operations on the Phase-2 retrieval indexes: rebuilding the HNSW index,
tuning `ef_search`, and re-embedding after an embedding-model change. The index build
params and runtime knobs are single-source in `Settings`/`get_index_params()`
(CLAUDE.md §7, ADR 0009) — change them there, never ad hoc in SQL.

## 1. Tune `ef_search` (no rebuild)

`ef_search` is a **runtime** probe-breadth knob; changing it needs no re-index. Higher
= better recall, more work. Set `SEARCH_EF_SEARCH` (default **200**) and restart the
API. There is no latency argument at current scale (≈11 ms p95 @ 100k/1536), so it is
set generously for recall; the real value should be tuned against a **labeled
real-embedding** set, not the synthetic sweep (ADR 0009 — synthetic recall is an
artifact).

- Measure on a representative corpus: `EMBEDDING_DIMENSION=<d> backend/.venv/bin/python
  scripts/drills/ef_search_sweep.py` prints the recall/p50/p95/p99 grid, the arm
  breakdown, and an EXPLAIN-ANALYZE confirmation that the HNSW node is used.
- **Invariant:** the vector arm must stay HNSW-backed. The benchmark asserts the
  production plan (`Seq Scan on chunks` fails CI). If you change the vector arm's
  **query shape**, re-read the `enable_sort = off` hazard note in ADR 0009 before
  trusting any ef number.

## 2. Rebuild the HNSW index

Needed after a bulk load, an `m`/`ef_construction` change, or index corruption. Build
params live in `get_index_params()` (`SEARCH_HNSW_M`, `SEARCH_HNSW_EF_CONSTRUCTION`);
change them there, then rebuild. Use the **fast path** — never build incrementally
over a large table:

```sql
-- Off-peak. CONCURRENTLY avoids an exclusive lock but is slower; plain is faster
-- under a maintenance window. ANALYZE afterwards or the planner runs on stale stats.
DROP INDEX IF EXISTS ix_chunks_embedding_hnsw;
SET maintenance_work_mem = '256MB';        -- or higher; speeds the build
CREATE INDEX ix_chunks_embedding_hnsw ON chunks
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
ANALYZE chunks;                            -- REQUIRED post-build (fresh planner stats)
```

- A **parallel** build uses dynamic shared memory; a default container's 64 MB
  `/dev/shm` aborts at 1536-dim (`DiskFullError`). Run Postgres with `shm_size >= 512
  MB` (dev compose sets `1gb`) or `SET max_parallel_maintenance_workers = 0` to build
  serially.
- Verify: `EXPLAIN` a vector query shows `Index Scan using ix_chunks_embedding_hnsw`
  and `pg_stat_user_tables.last_analyze` for `chunks` is post-build.

## 3. Re-embed after an embedding-model change

Changing the embedding model or its dimension is **not** an in-place operation — HNSW
indexes a fixed vector width, and mixing widths/curvatures corrupts ranking.

1. Set the new `EMBEDDING_*` config (and `EMBEDDING_DIMENSION` if it changed). A
   width mismatch fails fast (`EmbeddingDimensionError`) rather than storing
   wrong-width vectors.
2. Re-embed every document: `POST /api/v1/documents/{id}/reindex` (READY only) re-runs
   parse → chunk → embed → store. Re-ingest is idempotent (content-hash upsert), so
   re-indexing the whole corpus converges to exactly the current chunk set.
3. If the dimension changed, the column type changes too — that is a **migration**
   (new Alembic revision, two-step deploy per CLAUDE.md §7), then rebuild the HNSW
   index (step 2). Never alter the vector dimension in place.

## Knobs (Settings / env)

`SEARCH_EF_SEARCH` (runtime probe; default 200), `SEARCH_HNSW_ITERATIVE_SCAN`
(`strict_order`), `SEARCH_HNSW_M` / `SEARCH_HNSW_EF_CONSTRUCTION` (build params —
rebuild on change), `SEARCH_TOP_K`, `SEARCH_RRF_K`, `SEARCH_FTS_LANGUAGE`,
`SEARCH_MAX_RESULTS`. All single-source; `EMBEDDING_DIMENSION` changes trigger the
re-embed + migration procedure above.
