# Search domain

Permission-aware hybrid retrieval over the embedded chunks the knowledge domain
produces: a vector arm (pgvector HNSW, cosine) + a keyword arm (Postgres FTS, GIN)
whose results are combined with Reciprocal Rank Fusion, filtered by org_id +
collection ACLs **inside the SQL predicate, before ranking**, with metadata
filtering, cursor pagination, and source attribution. This is the only path to
chunk content (ADR 0009, ADR 0010).

## Public service interface (`service.py`)

`SearchService.search(actor, query) -> SearchResult` — the one use case (patterns
§1/§5): validate filters → embed the query (`ai.Embedder`) → run the ACL-filtered
vector + keyword arms → RRF-fuse (`rules.rrf_fuse`) → paginate → audit. Exposed at
`POST /api/v1/search`. The candidate SQL lives in `knowledge` behind the
`ChunkCandidateReader` port this domain owns (`ports.py`); `knowledge`'s
`ChunkRetrievalService` satisfies it structurally (DIP — ADR 0010).

## Invariants

- **ACL in the predicate, before ranking.** Every arm (vector, keyword, by-id)
  filters `org_id + EXISTS(collection_permissions for the user) + NOT superseded +
  embedding IS NOT NULL` before `ORDER BY`. A metadata filter can only intersect the
  ACL, never widen it. No retrieval path reaches chunk content without this predicate
  — proven by the isolation suite (cross-org) and the ACL suite (cross-user: by
  search, by metadata-filter abuse, by direct chunk id).
- **Index-backed, sub-linear.** The vector arm uses the HNSW index, the keyword arm
  the GIN index — EXPLAIN plan-assertion tests enforce it and a `Seq Scan on chunks`
  fails CI. The vector arm forces HNSW with a transaction-local `enable_sort = off` +
  `hnsw.iterative_scan = strict_order` (ADR 0009 — see its hazard note before
  changing the vector query shape).
- **Sequential arms.** The arms run in sequence on the shared connection so the
  vector arm's `enable_sort` toggle is restored before the keyword arm's `ts_rank`
  sort (ADR 0009). Per-arm sessions are the documented path if true concurrency is
  ever needed.
- **Fusion is pure and deterministic** (`rules.py`): RRF (`k = search_rrf_k`) keyed
  by chunk_id, tie-broken by `str(id)`. An empty result is a typed `200`
  (`{"results": [], "next_cursor": null}`), never an exception or a leak. The query
  text is never logged or echoed (audit records `result_count` only).
- **Server-capped pagination.** `limit` is capped at `search_max_results`; the cursor
  is a server-issued offset into the fused, capped candidate set.
- **Single-source tuning.** `ef_search`, `iterative_scan`, top-k, RRF constant, HNSW
  build params, and FTS language live only in `Settings`/`get_index_params()`;
  migration 0004 reads the build params from the same place (CLAUDE.md §7).

## Layering (`ports.py`, import-linter)

`main > search > knowledge > identity > ai > shared`. `search` never imports
`knowledge.repository` or `knowledge.models` (forbidden contract); it depends on the
`ChunkCandidateReader` port and the knowledge-owned candidate DTOs
(`ChunkCandidate`, `RetrievalFilters`).

## Known limitations

- `ef_search` is tuned for latency + recall *headroom* on a synthetic corpus;
  real-embedding recall must be tuned on a labeled set (ADR 0009 revisit trigger).
- Golden-set v0 is FTS-anchored (deterministic, no paid model); semantic-quality
  eval against a real embedding model is a later, separately-gated addition.
- The vector arm's HNSW coercion is a symptom fix for an ACL-join planner
  mis-estimate; the estimate-side fix (`CREATE STATISTICS`) is in `docs/backlog.md`.
