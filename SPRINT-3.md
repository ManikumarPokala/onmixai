# OnMixAI — Sprint 3 Specification (Phase 2: Search / Permission-Aware Retrieval)

Goal: a question goes in and the right chunks come back — fast, hybrid (vector +
keyword with rank fusion), and structurally incapable of returning a document the
requesting user cannot access. Retrieval is the ONLY entry point to chunk content;
`org_id` + collection ACLs are filtered inside the SQL predicate before similarity
ranking. No LLM completions yet (Phase 3 — the grounded-answer pipeline consumes
this retriever).

Execution rules: identical to Sprints 1–2 — strict task order, every VERIFY block
green before proceeding, one Conventional Commit per task,
CLAUDE.md/patterns.md/performance.md binding throughout. Review pauses: after
Task 3 (ACL-predicated queries + plan assertions), after Task 7 (100k benchmark +
HNSW tuning), and the final report. Otherwise run continuously. This sprint
activates two dormant CI gates: the **benchmarks** job and the **retrieval
golden-set eval**.

Sprint 3 exit criteria (roadmap Phase 2):
1. p95 < 3s for hybrid search on a seeded corpus of 100k chunks (measured, recorded in docs).
2. ACL zero-leak: a user without access to a collection retrieves zero of its chunks — by search, by direct chunk ID, and by metadata-filter abuse (test-verified).
3. recall@5 ≥ 0.85 on retrieval golden set v0; the eval is wired into CI as the Phase-2 regression gate.
4. Isolation suite extended to the chunks/embeddings retrieval path; all Sprint 1–2 CI gates remain green; new code ≥80% coverage.

---

## Task 1 — Index foundation: HNSW + FTS, single-source tuning config, migration 0004

Add the retrieval indexes to `chunks` and the tuning knobs that drive them, all
configured in one place (CLAUDE.md §7 — "HNSW parameters configured in one place";
§3.8 — no magic numbers).

Settings additions (typed, fail-fast), the single source of truth for both the
migration and the retriever: `search_hnsw_m: int = 16`,
`search_hnsw_ef_construction: int = 64`, `search_ef_search: int = 40` (runtime
probe; final default chosen in Task 7), `search_top_k: int = 60` (candidates per
arm), `search_rrf_k: int = 60` (RRF constant), `search_fts_language: str = "english"`,
`search_max_results: int = 50` (hard page cap). A small settings reader
(`get_index_params()`, mirroring `get_embedding_dimension()` in `shared/config.py`)
exposes m / ef_construction / fts_language to the migration without importing full
`Settings`.

Migration 0004 (reversible, owner-role-agnostic like 0002/0003):
- Add a generated column `chunks.content_tsv tsvector GENERATED ALWAYS AS
  (to_tsvector(:lang, content)) STORED` and a GIN index on it.
- Add an HNSW index on `chunks.embedding` using `vector_cosine_ops` with `m` and
  `ef_construction` from `get_index_params()`.
- The ACL/metadata predicate joins `chunks → documents → collection_permissions`
  and reuses the existing indexes (`ix_chunks_org_id_document_id_seq`,
  `ix_documents_org_id_collection_id_status`, the `collection_permissions` FK
  indexes) — no chunk-side `collection_id` denormalization.
- `downgrade()` drops both new indexes plus the column.

No new query paths yet — this task is the substrate. RLS already covers `chunks`
(migration 0002); no policy change.

**VERIFY**
```
cd backend && alembic upgrade head && alembic downgrade base && alembic upgrade head
# 0004 applies and reverses cleanly on a clean DB (CI migrations job covers this too)
pytest tests/shared/test_config.py -q     # search_* knobs present, typed, single-source reader agrees
mypy src/ tests/ && ruff check . && lint-imports
```
Commit: `feat(knowledge): hnsw + fts indexes and single-source search tuning (migration 0004)`

---

## Task 2 — Search domain skeleton: schemas, pure rules, ports

Create the `search/` domain (the layer ABOVE `knowledge`). Add to the import-linter
config: extend the layered contract to `main > search > knowledge > identity > ai >
shared`, and a forbidden contract `search` ↛ `knowledge.repository` /
`knowledge.models` (search uses knowledge's service interface only, CLAUDE.md §3.3),
with `allow_indirect_imports = "true"` like the existing knowledge↛identity contract.

- `search/schemas.py` — request `SearchQuery` (query text, optional `collection_id`,
  optional `format`, optional `created_after/created_before`, cursor, limit ≤
  `search_max_results`); internal `ScoredChunk` DTO (chunk id, document id,
  collection id, content, score, rank-per-arm, source attribution = filename + ref
  metadata) and `SearchResult` response (allow-list; never leaks `org_id`/embeddings).
- `search/rules.py` — PURE functions (zero I/O, branch-complete tests): `rrf_fuse`
  (reciprocal rank fusion over the per-arm ranked candidate lists, constant
  `search_rrf_k` injected), `dedupe_by_chunk` (a chunk surfaced by both arms appears
  once), and `validate_filters` (reject an empty/oversize page, contradictory date
  range, unknown format). Complexity annotations on each.
- `search/exceptions.py` — typed `AppError`s (e.g., `InvalidSearchFilterError` 422).
- The `ChunkCandidateReader` Protocol that search OWNS (the port it needs):
  `async vector_candidates(...) -> list[ScoredChunk]` and
  `async keyword_candidates(...) -> list[ScoredChunk]`, taking
  `(org_id, user_id, query_embedding|query_text, filters, top_k, ef_search)`.
  Knowledge's service satisfies it structurally in Task 3 (DIP, like `OrgQuotaReader`).
- `search/dependencies.py` — compose `SearchService` (wired fully in Task 4).

**VERIFY**
```
cd backend && pytest tests/search/test_rules.py -q
# RRF ordering correct (higher when ranked well by both arms); dedupe collapses
# cross-arm duplicates; validate_filters rejects every bad-filter branch; pure (no I/O)
mypy src/ tests/ && ruff check . && lint-imports   # new layered + search↛knowledge contracts KEPT
```
Commit: `feat(search): domain skeleton — schemas, pure RRF/filter rules, candidate-reader port`

---

## Task 3 — Permission-aware retriever: ACL-predicated vector + keyword arms  [PAUSE after VERIFY]

Implement the two candidate arms in `knowledge` (where chunk queries live) behind
the `ChunkCandidateReader` port search defined. Both arms filter inside the SQL
predicate, BEFORE ranking (CLAUDE.md §4 — "Retrieval without an ACL filter is a
security bug"):

```
WHERE c.org_id = :org
  AND c.embedding IS NOT NULL
  AND NOT d.superseded
  AND EXISTS (SELECT 1 FROM collection_permissions p
              WHERE p.collection_id = d.collection_id AND p.user_id = :user)
  AND (:collection_id IS NULL OR d.collection_id = :collection_id)
  AND (:format IS NULL OR d.content_type = :format)
  AND (:after  IS NULL OR d.created_at >= :after)
  AND (:before IS NULL OR d.created_at <= :before)
```

- Vector arm: `SET LOCAL hnsw.ef_search = :ef` then `ORDER BY c.embedding <=> :q
  LIMIT :k`. Keyword arm: `content_tsv @@ websearch_to_tsquery(:lang, :q) ORDER BY
  ts_rank(...) LIMIT :k`.
- `KnowledgeService` exposes a retrieval method implementing search's port; the
  ACL/metadata predicate is identical for both arms (one shared SQL builder in
  `knowledge/repository.py`).
- Plan-assertion tests (extend the §7 pattern): `EXPLAIN` shows an HNSW index scan
  for the vector arm and a GIN bitmap scan for the keyword arm; a `Seq Scan on
  chunks` fails the test.

**VERIFY**
```
cd backend && pytest tests/knowledge/test_retrieval_plan.py tests/search/test_retriever.py -q
# both arms ACL-filtered; EXPLAIN asserts HNSW scan (vector) and GIN scan (keyword),
# no Seq Scan on chunks; a chunk in a collection the user lacks access to never
# appears as a candidate from either arm (cross-org / cross-collection)
mypy src/ tests/ && ruff check . && lint-imports
```
Commit: `feat(knowledge,search): permission-aware vector + keyword candidate arms (ACL in predicate)`

**[PAUSE] Report:** the `EXPLAIN` output for BOTH arms (HNSW index scan and GIN scan
visible) and the cross-org candidate proof (a chunk the actor cannot access is
absent from the candidate set, shown via the predicate + a failing-if-present test).

---

## Task 4 — Hybrid search service + API

Wire the end-to-end retrieval pipeline (patterns §5 — composed, individually
testable steps; an empty result is a typed outcome, never an exception).

- `SearchService.search(actor, query)` (6-step anatomy, ≤40 lines): AUTHORIZE (org
  member) → LOAD (embed the query via ai's `Embedder`; resolve readable collections)
  → CHECK (`validate_filters`) → run both arms via the port (bounded, concurrent) →
  `rrf_fuse` + `dedupe_by_chunk` (pure) → RECORD (audit `search.executed`, no query
  text) → RETURN paginated `SearchResult` with source attribution.
- `POST /api/v1/search` (thin router): body = `SearchQuery`, returns `SearchResult`;
  cursor pagination with a hard server-side cap (`search_max_results`).
- Metadata filters (collection / format / date range) are applied in the SQL
  predicate (Task 3 builder), never as a post-filter — so a filter can never widen
  the ACL.
- `FakeEmbedder` (Phase 1) provides the query vector in tests; no LLM/network.

**VERIFY**
```
cd backend && pytest tests/search/test_search_api.py -q
# happy path returns fused, deduped, attributed results; empty-result is 200 with
# []; filters narrow results; pagination caps at search_max_results; loading/empty/
# error all typed; query text never logged/returned
mypy src/ tests/ && ruff check . && lint-imports
```
Commit: `feat(search): hybrid search service and POST /search with RRF, filters, pagination`

---

## Task 5 — ACL retrieval test suite (zero-leak) + isolation extension

Prove the security property three ways and extend the permanently-blocking
isolation suite to the retrieval path.

- `test_search_excludes_documents_user_cannot_access`: user with no permission on a
  collection gets zero of its chunks from `/search`.
- By direct chunk ID: any retrieve-by-id path is ACL-filtered identically (a known
  chunk id in an inaccessible collection returns nothing).
- By metadata-filter abuse: passing `collection_id` / `format` / date filters that
  name an inaccessible collection cannot surface its chunks (filter intersects, never
  widens, the ACL).
- `tests/isolation/`: org A vs org B retrieval — org A's actor retrieves zero org B
  chunks by search and by id; raw-count RLS already proven for `chunks` (Phase 1),
  now exercised through the retrieval predicate.

**VERIFY**
```
cd backend && pytest tests/search/test_acl.py tests/isolation/ -q
# all three abuse vectors return zero inaccessible chunks; isolation suite green
mypy src/ tests/ && ruff check . && lint-imports
```
Commit: `test(search): permission-aware retrieval zero-leak suite + isolation extension`

---

## Task 6 — Retrieval golden set v0 + eval harness (activate the eval gate)

Build a deterministic retrieval golden set and the eval that gates it.

- `tests/eval/golden_retrieval_v0.jsonl`: ≥50 `{query, expected_chunk_ref}` pairs
  over a seeded, checked-in corpus (generated fixtures, like Phase 1). The expected
  chunk is retrievable through the FULL pipeline (FTS arm + deterministic
  fake-embedder vector arm + RRF) — v0 validates retrieval plumbing/ACL/fusion/ranking
  deterministically; semantic-model recall is a later, separately-gated addition
  (documented in the eval README + ADR 0009).
- `src/search/eval.py` (or `tests/eval/`): runs each query through the real retrieval
  pipeline against the seeded corpus, computes recall@5 and MRR, prints a summary,
  exits non-zero if recall@5 < 0.85.
- `make eval-retrieval` target; activate CI gate #10 (the dormant golden-set eval)
  as a blocking job.

**VERIFY**
```
cd backend && make eval-retrieval
# prints recall@5 and MRR over >=50 pairs; recall@5 >= 0.85; deterministic (no network)
pytest tests/eval/ -q && mypy src/ tests/ && ruff check .
```
Commit: `test(search): retrieval golden set v0 + make eval-retrieval (recall@5/MRR gate)`

---

## Task 7 — 100k-chunk benchmark + HNSW tuning  [PAUSE after VERIFY]

Measure the p95 budget at scale and choose the runtime probe default; activate the
dormant benchmarks job.

- A seeding helper bulk-inserts 100k embedded chunks (deterministic vectors, COPY/bulk
  — never per-row awaits; performance.md §5) across several orgs/collections so the
  ACL predicate is exercised.
- `tests/benchmarks/test_search_bench.py`: hybrid `/search` p95 over N queries
  < 3s @ 100k (performance.md §2 budget); marked so it runs in the benchmarks job, not
  the default suite.
- `ef_search` sweep: for a grid (e.g. 10/20/40/80/120/200) record approximate recall
  (HNSW vs exact-KNN baseline on the seeded corpus) and p95 latency; choose the
  default `search_ef_search` balancing recall and the p95 budget; set it in Settings.
- Activate the benchmarks CI job (currently a NOTE in `ci.yml`).

**VERIFY**
```
cd backend && pytest tests/benchmarks/test_search_bench.py -q
# p95 < 3s @ 100k chunks (printed); ef_search sweep table printed (recall vs p95)
mypy src/ tests/ && ruff check .
```
Commit: `perf(search): 100k hybrid-search benchmark, ef_search tuning, benchmarks gate`

**[PAUSE] Report:** the full `ef_search` tuning table (recall vs p95 per setting) and
the chosen default, with the p95@100k measurement.

---

## Task 8 — Phase 2 exit: docs, ADRs, full gates, final report

- ADR 0009 (HNSW + FTS hybrid + RRF: cosine ops, single-source params, the chosen
  `ef_search` default and the recall/p95 trade-off; golden-set v0 determinism).
- ADR 0010 (permission-aware retrieval: the `search > knowledge` layer, the
  `ChunkCandidateReader` port / DIP, ACL-in-predicate-before-ranking as the only
  retrieval entry point).
- `src/search/README.md` (responsibility, the retrieval port, invariants: ACL in
  predicate, hybrid+RRF, no leak); runbook `docs/runbooks/reindex-embeddings.md`
  (HNSW rebuild / `ef_search` tuning / re-embed on model change); README quickstart
  note for the search endpoint.
- Full local CI on the closing commit incl. both newly-active gates.

**VERIFY**
```
cd backend && ruff check . && ruff format --check . && mypy src/ tests/ && lint-imports
./scripts/run-tests.sh                                   # two-pass suite, coverage >=80%
.venv/bin/pytest tests/isolation/ -q                     # blocking isolation job
alembic downgrade base && alembic upgrade head           # reversible incl. 0004
make eval-retrieval                                      # recall@5 >= 0.85
pytest tests/benchmarks/test_search_bench.py -q          # p95 < 3s @ 100k
git grep -nE "TODO|FIXME" -- src/ ; test $? -ne 0
```
Commit: `docs,test: phase 2 exit — search ADRs, README, runbook, benchmark + eval gates`

---

## Phase 2 robustness checklist (final gate)

- [ ] Retrieval is the only path to chunk content; every arm filters org_id + collection ACL inside the SQL predicate, before ranking
- [ ] User without collection access gets zero chunks: by search, by direct chunk ID, and by metadata-filter abuse
- [ ] Vector arm uses the HNSW index; keyword arm uses the GIN index — proven by EXPLAIN plan-assertion tests; a Seq Scan on chunks fails CI
- [ ] `ef_search`, top-k, RRF constant, HNSW build params, FTS language live only in Settings (single source); migration reads build params from the same place
- [ ] Hybrid fusion is pure and deterministic; an empty result is a typed 200, not an exception or a leak
- [ ] Metadata filters intersect the ACL, never widen it; pagination is server-capped
- [ ] p95 < 3s @ 100k chunks, measured and recorded; ef_search default justified by the recall/latency table
- [ ] recall@5 ≥ 0.85 on golden set v0; eval wired into CI as the Phase-2 regression gate
- [ ] Isolation suite extended to the chunks/embeddings retrieval path, green
- [ ] No per-row awaits in the candidate or seeding paths; complexity annotations on retrieval/fusion code; query text never logged or returned
