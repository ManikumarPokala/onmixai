# Recommendation latency — Phase 5, Task 9

Drill: `scripts/drills/recommendation_latency.sh` (→ `scripts/drills/recommendation_latency.py`).

## What is measured

End-to-end latency of the recommendation pipeline's hot path: retrieve → confidence band →
decline gate → **one** blocking structured generation → justification grounding. Unlike chat,
a recommendation is a **single non-streaming structured call**, so the wall-clock is dominated
by that one `gateway.complete` round-trip. Retrieval is a fixed in-memory source (the real
hybrid `/search` hot path is benchmarked separately — p95 < 3 s @ 100k chunks, ADR 0009) and
the band + grounding work is pure and O(j·m), negligible.

## Stub delay model (the caveat)

Generation is served by the in-process `infra/dev/llm_stub.py`, which sleeps a fixed per-call
delay in JSON mode (env-tunable):

| Knob | Default | Models |
|---|---|---|
| `STUB_JSON_MS` | 600 ms | one structured completion of a mid-size hosted LLM |

So the recorded number ≈ the modeled structured-call latency plus the bounded
retrieval+band+grounding constant. This is **harness + mechanics correctness**, not real-model
quality or speed — the same honesty caveat as the retrieval/generation/chat golden sets and the
chat-latency note. **Revisit trigger:** re-measure against a real provider when one is
configured (a recommendation has no streaming, so its p95 is essentially the provider's
full-completion latency for the structured output).

The gate (`p95 < 10 s`) is a **generous mechanics-regression guard**, not the product budget:
it catches an accidental extra round-trip or an N+1 in the path. The product budget is governed
by real-model completion latency, re-measured later.

## Recorded run

`RECOMMENDATION_LATENCY_N=100`, delay model `json=600 ms`, local dev (Apple silicon),
2026-06-08:

| Metric | p50 | p95 | Budget |
|---|---|---|---|
| end-to-end | 611 ms | 616 ms | < 10 s ✓ |

The ~11 ms over the modeled 600 ms is the localhost HTTP round-trip + litellm overhead +
retrieval/band/grounding — confirming the single structured call dominates and the rest of the
path is negligible. A below-floor / empty retrieval **declines before generation** (zero model
spend), so a declined recommendation is strictly faster than the numbers above.

## Related Phase-5 latency (consolidated)

Report generation + 30-page PDF export timing is recorded in
[`report_export.md`](report_export.md): the export **render** is 569 ms for 45 pages (< 10 min
budget, ~1000× headroom); report **generation** is the same fixed knowledge→report graph with a
single structured call per run, so its generation latency tracks the recommendation number
above (one structured completion), under the same stub caveat.
