# OnMixAI — Performance & Complexity Standards (docs/performance.md)

This document makes algorithmic efficiency a reviewable, enforceable property — not an afterthought. Every non-trivial function declares its complexity; every hot path has a budget; every budget has a benchmark. Code that is correct but algorithmically careless does not merge.

---

## 1. Complexity Annotations — Mandatory

Every function containing a non-trivial algorithm (anything beyond a single pass or a direct lookup) declares its complexity in the docstring, where n is named explicitly:

```python
def fuse_rankings(vector_hits: list[ScoredChunk], keyword_hits: list[ScoredChunk],
                  k: int = 60) -> list[ScoredChunk]:
    """Reciprocal rank fusion of two ranked lists.

    Time:  O(v + w + m log m) — v, w = input list sizes; m = distinct chunks after merge.
    Space: O(m) — one score accumulator per distinct chunk.
    """
    scores: dict[UUID, float] = {}
    for rank, hit in enumerate(vector_hits):          # O(v), dict upsert O(1)
        scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank + 1)
    for rank, hit in enumerate(keyword_hits):         # O(w)
        scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(merge(scores), key=lambda c: c.score, reverse=True)   # O(m log m)
```

Rules:
- The annotation is reviewed like the code. A wrong annotation is a defect.
- If you cannot state the complexity, you do not understand the function — rewrite until you can.
- Reviewer question for every loop: "what bounds this?" Unbounded input → the function must take a limit, paginate, or stream.

## 2. Hot-Path Budgets (benchmarked, not aspirational)

| Path | Target complexity | Latency budget | Enforced by |
|---|---|---|---|
| Hybrid search (per query) | O(log N) index probe + O(k log k) fusion, k ≤ 200 | p95 < 3s @ 1M chunks | `tests/benchmarks/test_search_bench.py` |
| ACL filter in retrieval | In-index predicate (no post-filter loop over results) | included above | query-plan test (§5) |
| Context assembly (chat) | O(t + c) single pass over turns t + chunks c | < 50 ms | benchmark |
| Chunking (per document) | O(p) single pass over parsed content | 100-page PDF < 30s CPU | benchmark fixture |
| Embedding upsert | O(c/b) batched, b = 64–128 per API call | — | code review + adapter test |
| Token metering check | O(1) — single indexed row read/increment | < 5 ms | benchmark |
| Audit append | O(1) insert, fire-and-forget queue if needed | non-blocking to request | design review |

Benchmarks run in CI on seeded fixtures; a budget regression fails the build like a failing test.

## 3. Algorithmic Rules (Python)

**Required:**
- Membership tests against collections → `set`/`dict` (O(1)), never `list` (O(n)). Any `x in some_list` inside a loop is an instant review flag — that is O(n·m).
- Merging/joining two datasets → build a dict index of one side first (O(n + m)), never nested loops (O(n·m)).
- Deduplication → `dict.fromkeys` / set with a defined key, single pass.
- Top-k from a large set → `heapq.nlargest(k, items)` O(n log k), not full sort O(n log n) when k ≪ n.
- String building in loops → `"".join(parts)`, never `+=` (O(n²) total).
- Large sequences processed once → generators/iterators, not materialized lists.

**Banned (PR rejection):**
- Nested loops over two unbounded collections without a justifying comment + annotation
- `list.insert(0, x)` / `pop(0)` in loops → use `collections.deque`
- Quadratic list concatenation (`result += [x]` patterns at scale)
- Recursion over user-controlled depth (stack overflow = crash) → iterative with explicit stack
- Sorting inside a loop when one sort outside suffices

## 4. Memory & Space Discipline

- **Streaming over buffering.** A 50 MB upload is streamed to object storage in chunks (`aiofiles` / multipart streaming) — never `await file.read()` into RAM. PDF parsing processes page-by-page; chunk → embed → store flows in batches of ~100 chunks, releasing each batch before the next.
- **Space annotations** required alongside time (§1). A function whose space is O(n) over an unbounded n must batch or stream.
- **Bounded caches only.** Every in-process cache declares a max size + eviction (`functools.lru_cache(maxsize=...)`, TTL caches). An unbounded module-level dict cache is a memory leak with a delay timer — banned by patterns.md §10 and here.
- **DB result sets are bounded** by construction (repository hard caps, patterns.md §2). `scalars().all()` on an unfiltered table never appears.
- **Embeddings in memory**: never hold a whole document's vectors at once for large docs; the upsert pipeline carries one batch at a time. Peak memory for ingestion is O(batch), independent of document size.
- **Worker memory ceiling**: ingestion workers run with an explicit memory limit (container); a single pathological file must OOM one task (→ FAILED with reason), never the worker fleet.

## 5. Database Complexity (where most "slow" actually lives)

- **Every query in the codebase has an index that serves it.** New repository method → check the plan. CI includes plan-assertion tests for the hot queries: `EXPLAIN (FORMAT JSON)` must show index/HNSW scan, fail on sequential scan over tenant tables beyond trivial size.
- ACL + org_id filtering happens **in the SQL predicate** (composite indexes on `(org_id, ...)`), never as a Python post-filter — post-filtering retrieved rows is both a performance bug and a correctness risk (under-fetching).
- N+1 is banned and tested: list-endpoint tests assert query count (`sqlalchemy` event counter) — a list of 50 items must not issue 51 queries.
- Batch writes: chunk/embedding inserts use `insert().values([...])` / `COPY`-style bulk paths, never per-row awaits in a loop.
- pgvector: HNSW `m` / `ef_construction` / `ef_search` set in one config location with the measured recall/latency trade-off documented in an ADR; changes require re-running the retrieval benchmark.

## 6. Concurrency & Throughput

- Independent awaits are gathered: `asyncio.gather` for parallel embedding batches / parallel provider health checks — never sequential awaits in a loop when calls are independent. Concurrency is always bounded (`asyncio.Semaphore`) to respect provider rate limits.
- CPU-bound work (OCR, parsing) never runs on the event loop — worker processes only. One slow parse must never stall API latency.
- Backpressure: the ingestion queue has a depth metric and the upload endpoint sheds load (429) past a threshold, instead of accepting work it cannot finish.

## 7. Measurement Discipline ("perfect logic" is proven, not asserted)

1. **Benchmarks are tests.** `tests/benchmarks/` runs in CI against seeded fixtures with thresholds from §2. Regressions block merge.
2. **Profile before optimizing.** Any optimization PR includes before/after numbers (`py-spy` / `cProfile` output or benchmark deltas). Optimization without measurement is rejected — as is pessimization without notice.
3. **Big-O first, micro-opt last.** Fix the algorithm class before tuning constants. A reviewed O(n²) → O(n log n) beats any amount of caching bolted onto the wrong algorithm.
4. **Latency is traced end-to-end** (Phase 3 tracing): every slow request is decomposable into retrieve / assemble / generate / validate timings. "It's slow" is never the final diagnosis.

## 8. PR Review Checklist (complexity section — appended to CLAUDE.md §12 DoD)

- [ ] Non-trivial functions carry Time/Space annotations; annotations verified correct
- [ ] No banned shapes from §3; every loop's bound identified
- [ ] New queries: index exists, plan asserted for hot paths, no N+1 (query-count test where applicable)
- [ ] Large data flows stream/batch; peak memory independent of input size
- [ ] Independent I/O gathered with bounded concurrency
- [ ] Hot-path changes accompanied by benchmark results within §2 budgets
