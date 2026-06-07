# ADR 0009 — Hybrid Retrieval: HNSW + Postgres FTS + Reciprocal Rank Fusion

Status: Accepted (2026-06-07)

## Context

Phase 2 needs fast, relevant retrieval over chunk embeddings + text, inside one
Postgres (no separate vector store this phase). Two recall failure modes matter:
pure-vector misses exact-term/keyword matches; pure-keyword misses paraphrases.
The hot-path budget is p95 < 3s @ 100k chunks (performance.md §2). Index build
params and the runtime probe must live in one place (CLAUDE.md §7). Retrieval is
permission-aware: the org_id + collection-ACL predicate is applied **before**
ranking, inside the SQL (CLAUDE.md §4, ADR 0010).

## Decision

**Two arms, fused.** Vector arm: pgvector **HNSW** on `chunks.embedding`
(`vector_cosine_ops`; OpenAI embeddings are cosine-normalized), `m=16`,
`ef_construction=64`, runtime `hnsw.ef_search` set per query. Keyword arm: a
`STORED` generated `tsvector` column (`content_tsv`) + **GIN** index, queried with
`websearch_to_tsquery` and ranked by `ts_rank`. Both arms apply the org_id +
collection-ACL predicate *before* ranking. Results are combined with **Reciprocal
Rank Fusion** (`k = search_rrf_k = 60`), a pure, deterministic step.

**Single source of truth.** `m`, `ef_construction`, FTS language live in
`get_index_params()`; the runtime knobs (`search_ef_search`,
`search_hnsw_iterative_scan`, `search_top_k`, `search_rrf_k`, `search_max_results`)
live in `Settings`. Migration 0004 reads the build params from the same place, so
the index and the runtime can't drift.

**Making the ACL-filtered query actually use the index (the load-bearing decision).**
A naïve `… WHERE org_id = :o AND EXISTS(acl) ORDER BY embedding <=> :q LIMIT k` does
**not** use the HNSW index at scale. The planner mis-estimates the ACL join's
cardinality (measured: estimate `rows=250` vs actual `rows=100000`, a 400× under-
estimate) and concludes an exact top-N sort over the whole org is cheaper, producing
`Limit → Sort → … → Index Scan using ix_chunks_org_id_document_id_seq (rows=100000)`
— an O(n) exact scan. It is *correct* (recall 1.0) and meets the budget at 100k
(~440 ms), but it is **not** the ANN index and breaks the budget at ~1M. `pgvector`'s
`hnsw.iterative_scan` alone does not change the choice. The vector arm therefore sets
three transaction-local settings (`ChunkRepository.search_vector`):

1. `hnsw.ef_search` — probe breadth.
2. `hnsw.iterative_scan = strict_order` (pgvector ≥ 0.8) — keep fetching ordered
   candidates until `top_k` survive the ACL predicate, so a partial-access user is
   never silently short-changed; `strict_order` preserves exact distance order (RRF
   ranks depend on it).
3. `enable_sort = off` — remove the exact-sort alternative so the planner must take
   distance order from the HNSW index. Deterministic across all `ef_search` (a query
   restructure was tried — `document_id IN (subquery)` — but it flips back to the
   exact sort at `ef ≥ 40`, so it is not robust at the configured probe).

`enable_sort` is **restored to `on` immediately after the vector query** so it never
reaches the keyword arm's `ts_rank` sort, which shares the request transaction. For
that restore to be safe the two arms run **sequentially**, not concurrently
(`SearchService`); they share one connection (which SQLAlchemy serializes anyway), so
no parallelism is lost, and both arms are tens of milliseconds at 100k. A permanent
regression test (`test_ef_search_guc_applies_in_transaction`) asserts the GUC reaches
the query's transaction and that `enable_sort` is restored; the benchmark asserts the
production plan is HNSW-backed (`Seq Scan on chunks` fails CI) so a silent regression
to the exact scan cannot pass merely by meeting the budget.

**`ef_search` default = 200.** Tuning sweep on a seeded **100k-chunk, 1536-dim**
corpus (`scripts/drills/ef_search_sweep.py`; ANALYZE confirmed post-seed,
`n_live_tup=100002`), HNSW path forced as above, recall@5 vs an exact-KNN baseline
(`enable_indexscan = off`), 40 queries:

| ef_search | recall@5 | p50 ms | p95 ms | p99 ms |
|----------:|---------:|-------:|-------:|-------:|
| 10  | 0.510 | 5.8 | 447.3¹ | 454.3 |
| 20  | 0.140 | 2.5 | 4.0 | 4.4 |
| 40  | 0.225 | 2.8 | 3.2 | 3.5 |
| 80  | 0.310 | 4.4 | 5.6 | 57.4 |
| 120 | 0.365 | 5.5 | 6.4 | 6.7 |
| 200 | 0.465 | 9.2 | 11.0 | 11.2 |

¹ single cold-start query (plan/cache warm-up); p50 at ef=10 is 5.8 ms.

Arm breakdown at the chosen default (per query, p50): vector ≈ 11 ms, keyword
≈ 2.1 ms, fusion ≈ 0.05 ms. The HNSW node cost/time scales with ef as expected
(EXPLAIN ANALYZE: ef=10 → 2.3 ms / startup cost 1143; ef=200 → 14.7 ms / startup
cost 9556) — proof the probe breadth reaches the index, not just the session.

**Rationale.** Latency is a **non-constraint**: every setting clears the 3 s budget
by >2.9 s (p95 ≤ 11 ms). Recall@5 is **low and noisy (0.14–0.51) and cannot be used
to pick `ef` on this corpus** — uniform-random 1536-dim vectors have no neighborhood
structure (distance concentration), so the exact top-5 are near-tied with thousands
of others and HNSW returns an equally-valid but low-overlap set. (The earlier
recall=1.000 was an artifact of the *accidental exact scan*, not index quality.)
Recall still rises monotonically with `ef` (0.14 → 0.47 over ef 20→200), so we pick
the recall-safest end of the grid, **200**, which is also the empirical best in the
sweep — at 11 ms p95 it costs nothing. Real-embedding recall must be tuned on a
labeled set (revisit trigger below).

**Golden-set v0 is FTS-anchored** (tests/eval): recall@5 ≥ 0.85 is gated
deterministically through the pipeline without a paid model; semantic-recall eval
against a real embedding model on a labeled set is a later, separately-gated addition.

## Consequences

- The vector arm is now O(log n): ~2–15 ms p50 @ 100k/1536 on a laptop container
  (vs ~440 ms for the prior exact scan), and it scales sub-linearly toward 1M.
- `enable_sort = off` is a deliberate, documented planner directive scoped to the
  vector statement and restored before the keyword arm — not a workaround hiding a
  failure, but the only deterministic way to defeat the ACL-join cardinality
  mis-estimate. If a future PG/pgvector improves the cost model (or `pg_hint_plan`
  is adopted) this can be revisited.
- The arms run sequentially on one connection. True per-arm parallelism would need a
  connection each; it is a documented future optimization, unneeded for the budget.
- The GIN keyword arm: at ~100k it is chosen on the real arm (`Bitmap Index Scan
  using ix_chunks_content_tsv_gin`, measured); below ~30k the planner may apply `@@`
  as a filter over the org btree (still index-backed — never a `Seq Scan on chunks`).
- A parallel HNSW build needs > a default container's 64 MB `/dev/shm`; dev compose
  sets `shm_size: 1gb` and the benchmark builds serially (small at test dim).

**Revisit triggers** (re-tune `ef_search`/`m`, or move to a dedicated vector store):
- corpus exceeds **~5M chunks**, or
- production hybrid **p95 > 2s**, or
- real-embedding **recall@5 < 0.85** on a labeled set — the synthetic sweep cannot
  detect this (it is the known blind spot of golden-set v0 and the reason `ef_search`
  is set generously rather than tuned to a synthetic recall number).
