# Runbook — chat quality regression

When the chat golden-set gate (`eval-chat`) goes red, chat latency regresses, or the feedback
queue shows a spike of thumbs-down. Goal: localize the regression to a layer (retrieval,
rewrite, grounding, generation, or the harness itself) and decide ship / hold.

## 1. Reading the eval deltas (`make eval-chat`)

The eval prints one line:

```
[chat v0] n=44 answerable_recall=… refusal_correctness=… (wrong_refusals=… wrong_answers=…)
          faithfulness_mean=… citation_validity=… citation_precision=… phantom_invention_rate=…
```

Map a regression to its layer:

| Symptom | Likely layer | Where to look |
|---|---|---|
| `wrong_refusals` > 0 (answerable now refused) | retrieval or grounding | did retrieval stop returning the planted chunk (search/RRF/ACL change)? did the grounding marker rule tighten? |
| `wrong_answers` > 0 (unanswerable now answered) | grounding | the zero/phantom-marker refusal rule weakened, or the stub/model started citing unsupported sources |
| `faithfulness_min` < 0.9 | generation / prompt | `grounded_answer` template change, or model drift — check the prompt version + changelog |
| `citation_validity` < 1.0 or `phantom_invention_rate` > 0 | grounding | the phantom-strip / `chat_max_phantom_fraction` rule changed; a phantom leaked into the validated set |
| `citation_precision` < 1.0 | retrieval ranking | the cited source is no longer the supporting chunk (ranking/RRF change) |
| determinism assert fails (`run1 != run2`) | non-determinism leaked in | a clock/random/nonce reached a compared field; the stub or pipeline became order-dependent |

Reproduce locally: `make eval-chat` (runs the full path twice). The set is deterministic
against the stub — a red gate is a real routing/rule change, not flakiness. The gate is
harness-correctness, not model quality; a *real-model* faithfulness drop is only visible once
a provider is configured (then re-baseline per CLAUDE.md §9 — golden sets only grow).

## 2. Bisect by layer

1. **Retrieval** — `make eval-retrieval` (recall@5/MRR). If this is also red, the regression is
   upstream in search; fix there first.
2. **Grounding rules** — `pytest tests/conversation/test_grounding.py` (branch-complete:
   zero-marker, minority-phantom-strip, parity-refuse).
3. **Pipeline routing** — `pytest tests/conversation/test_pipeline.py` (the cite-or-refuse
   matrix; infra-error propagation vs content refusal).
4. **Generation/prompt** — `make eval-generation` (faithfulness via judge); inspect
   `src/ai/prompts/grounded_answer/meta.yaml` changelog + `body_sha256` for an unreviewed edit.

## 3. Latency regression

`bash scripts/drills/chat_latency.sh` → first-token + full p50/p95. Budgets: first-token p95 <
3 s, full p95 < 15 s. The stub delay model (`STUB_STREAM_FIRST_MS`, `STUB_STREAM_TOKEN_MS`) is
documented in `docs/benchmarks/chat_latency.md`. First-token regressions point at pre-generation
overhead (retrieval/assembly — cross-check the `/search` benchmark and the < 50 ms assembly
budget); full-response regressions track answer length × per-token cost.

## 4. Feedback-queue triage

Thumbs-down feedback (`message_feedback.rating = 'down'`) is the human signal the eval can't
see. To triage a spike:

1. Pull recent down-rated assistant messages with their `trace_id` (the join key to the
   gateway trace: prompt template + version, model, retrieved source IDs).
2. For each, follow the trace: were the *right* sources retrieved? did the answer cite them?
   was it a borderline refusal the user wanted answered (or vice versa)?
3. Cluster the failures: retrieval misses → add retrieval golden cases; grounding/faithfulness
   → add chat golden cases (answerable/refusal/citation as appropriate) and re-baseline. Golden
   sets only grow — every confirmed regression ships a new case that fails before the fix.

## 5. Ship / hold

- A red `eval-chat`, `eval-retrieval`, or `eval-generation` gate **blocks merge** — no override.
- Latency over budget blocks merge (`chat_latency` drill / benchmark job).
- A prompt or model-config change without an eval run does not merge (CLAUDE.md §9/§11 #10).
- A feedback-driven fix merges only with its regression case added to the appropriate golden
  set.
