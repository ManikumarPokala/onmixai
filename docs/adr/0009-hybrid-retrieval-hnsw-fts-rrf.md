# ADR 0009 — Hybrid Retrieval: HNSW + Postgres FTS + Reciprocal Rank Fusion

Status: Accepted (2026-06-05)

## Context

Phase 2 needs fast, relevant retrieval over chunk embeddings + text, inside one
Postgres (no separate vector store this phase). Two recall failure modes matter:
pure-vector misses exact-term/keyword matches; pure-keyword misses paraphrases.
The hot-path budget is p95 < 3s @ 100k chunks (performance.md §2). Index build
params and the runtime probe must live in one place (CLAUDE.md §7).

## Decision

**Two arms, fused.** Vector arm: pgvector **HNSW** on `chunks.embedding`
(`vector_cosine_ops`; OpenAI embeddings are cosine-normalized), `m=16`,
`ef_construction=64`, runtime `hnsw.ef_search` set per query. Keyword arm: a
`STORED` generated `tsvector` column (`content_tsv`) + **GIN** index, queried with
`websearch_to_tsquery` and ranked by `ts_rank`. Both arms apply the org_id +
collection-ACL predicate *before* ranking (ADR 0010). Results are combined with
**Reciprocal Rank Fusion** (`k = search_rrf_k = 60`), a pure, deterministic step.

**Single source of truth.** `m`, `ef_construction`, FTS language live in
`get_index_params()`; the runtime knobs (`search_ef_search`, `search_top_k`,
`search_rrf_k`, `search_max_results`) live in `Settings`. Migration 0004 reads the
build params from the same place, so the index and the runtime can't drift.

**`ef_search` default = 40.** Tuning sweep on a seeded **100k-chunk, 1536-dim**
corpus (`scripts/drills/ef_search_sweep.py`; ANALYZE confirmed post-seed,
`n_live_tup=100002`), recall@5 vs an exact-KNN baseline, 40 queries:

| ef_search | recall@5 | p50 ms | p95 ms | p99 ms |
|----------:|---------:|-------:|-------:|-------:|
| 10  | 1.000 | 541.7 | 714.1 | 881.2 |
| 20  | 1.000 | 600.4 | 929.2 | 1399.6 |
| 40  | 1.000 | 677.1 | 893.0 | 987.4 |
| 80  | 1.000 | 541.9 | 787.4 | 888.9 |
| 120 | 1.000 | 558.2 | 870.5 | 1109.8 |
| 200 | 1.000 | 540.7 | 686.2 | 757.5 |

Arm breakdown at the chosen default (per query, p50): vector ≈ 600 ms, keyword
≈ 1.8 ms, fusion ≈ 0.02 ms — the ANN probe dominates; FTS and fusion are free.

**Rationale.** recall@5 is saturated (1.000) across the whole grid because the
sweep corpus is uniform-random vectors, which don't stress ANN recall — so the
sweep bounds *latency*, not recall, and every setting clears the 3s budget with
>2s of headroom (p95 ≤ 0.93s). We therefore do **not** pick the mechanically
minimal `ef_search=10`; we pick **40** as a conservative middle that keeps a large
recall safety margin for *real* (clustered, anisotropic) embeddings — where recall
rises with `ef_search` — while staying ~0.9s p95. Margin over the 0.85 recall floor
on this corpus: **+0.15**; latency margin under budget: **~2.1s**.

**Golden-set v0 is FTS-anchored** (ADR-adjacent, tests/eval): recall@5 ≥ 0.85 is
gated deterministically through the pipeline without a paid model; semantic-recall
eval against a real embedding model on a labeled set is a later, separately-gated
addition.

## Consequences

- The GIN keyword arm wins on cost only once a tenant's chunk count is large: at
  ~100k it is chosen on the real arm (`Bitmap Index Scan using
  ix_chunks_content_tsv_gin`, measured); below ~30k the planner may apply `@@` as a
  filter over the org btree (still index-backed — never a `Seq Scan on chunks`).
- The vector arm (~600 ms p50 @ 100k/1536 on a laptop container) dominates latency;
  there is ample headroom, but it is the first thing to optimize if the budget
  tightens.
- A parallel HNSW build needs > a default container's 64 MB `/dev/shm`; dev compose
  sets `shm_size: 1gb` and the benchmark test builds serially (small at test dim).

**Revisit triggers** (re-tune `ef_search`/`m`, or move to a dedicated vector store):
- corpus exceeds **~5M chunks**, or
- production hybrid **p95 > 2s**, or
- real-embedding **recall@5 < 0.85** on a labeled set (the synthetic sweep cannot
  detect this — it is the known blind spot of golden-set v0).
