# Chat streaming latency — Phase 4, Task 9

Drill: `scripts/drills/chat_latency.sh` (→ `scripts/drills/chat_latency.py`).

## What is measured

First-token and full-response latency of the grounded chat pipeline's **streaming** path,
from the SSE-event side: the drill runs `GroundedAnswerPipeline.answer_stream` (rewrite →
retrieve → assemble → `complete_stream`) `N` times and records, per turn:

- **first-token** — wall-clock from request start to the first `token` event;
- **full** — wall-clock to the terminal event (`done` / `refusal`).

It reports p50/p95 for both and enforces the budgets **first-token p95 < 3 s** and
**full p95 < 15 s**.

## Stub delay model (the caveat)

Generation is served by the in-process `infra/dev/llm_stub.py`, which streams the completion
as OpenAI-style SSE chunks with an injected delay model (env-tunable):

| Knob | Default | Models |
|---|---|---|
| `STUB_STREAM_FIRST_MS` | 400 ms | time-to-first-token of a mid-size hosted LLM |
| `STUB_STREAM_TOKEN_MS` | 25 ms | per-token (≈40 tok/s) inter-token delay |

Retrieval is a fixed in-memory source, so the drill isolates the **generation-streaming
component**, which dominates first-token and scales the full response with answer length.
The retrieval hot path is benchmarked separately (hybrid `/search` p95 < 3 s @ 100k chunks,
ADR 0009) and context assembly is budgeted < 50 ms (§8); end-to-end first-token adds those
bounded constants on top of the numbers below.

This is **harness + mechanics correctness**, not real-model quality or speed — the same
honesty caveat as the retrieval/generation/chat golden sets. **Revisit trigger:** re-measure
against a real provider when one is configured (and when the typical answer length is known —
full-response latency is ≈ `first_ms + (tokens − 1) × token_ms`).

## Recorded run

`CHAT_LATENCY_N=100`, delay model `first=400 ms, per_token=25 ms`, answer ≈ 18 tokens,
local dev (Apple silicon), 2026-06-08:

| Metric | p50 | p95 | Budget |
|---|---|---|---|
| first-token | 411 ms | 441 ms | < 3 s ✓ |
| full | 896 ms | 920 ms | < 15 s ✓ |

Extrapolation (same delay model): a 200-token answer ≈ `400 + 199 × 25` ≈ **5.4 s** full —
still within the 15 s budget. First-token is independent of answer length (≈ `STUB_STREAM_FIRST_MS`
plus the small retrieval+assembly constant), so it stays well under 3 s.
