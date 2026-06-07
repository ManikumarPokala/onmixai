# AI domain — the LLM gateway

The single, disciplined doorway to every LLM: routed, resilient, and (Tasks 5–8)
metered, budget-enforced, traced, and guarded. No feature talks to a provider
directly — they depend on the `LLMGateway` Protocol (`gateway.py`) and its typed,
immutable value objects. The provider SDK (litellm) is importable only from
`ai/adapters/` (import-linter). This README grows with the sprint; Task 4 establishes
the gateway contract, the resilience taxonomy, and the wall-clock bound.

## Public interface (`gateway.py`)

`LLMGateway.complete(*, prompt, ctx, model=None, response_schema=None) -> Completion`.
Value types: `RenderedPrompt` (template name + version + variables_hash + messages),
`GatewayContext` (org_id, user_id, feature, request_id, source_chunk_ids), `ModelRef`,
`Completion` (text, model_used, token counts, finish_reason, trace_id). Tests use
`FakeGateway`; the real adapter is `adapters/litellm_gateway.py`.

## Error taxonomy (the reference classification, patterns.md §9)

A typed result for every outcome — never a hang, never fabricated output on failure.

| Outcome | Trigger | Maps to |
|---|---|---|
| **Retryable** (retry → fallback → 503) | `Timeout`, `RateLimitError` (429), `ServiceUnavailableError` (503), `InternalServerError` (500), `APIConnectionError`; and any `APIError` with status **408** or **429** | `UpstreamUnavailableError` (503) once retries + the whole fallback chain are exhausted |
| **Non-retryable rejection** (immediate, no retry, no fallback) | `BadRequestError`, `ContentPolicyViolationError`, `ContextWindowExceededError`, `AuthenticationError`, `PermissionDeniedError`, `NotFoundError`; any other `APIError` with a 4xx status **other than 408/429** | `UpstreamRejectedError` (422) — safe client message, full provider detail logged only |
| **Schema invalid** (JSON mode) | response fails the `response_schema` after one bounded re-ask | `UpstreamRejectedError("SCHEMA_VALIDATION_FAILED")` |
| **Budget exhausted** | period total ≥ hard limit, checked BEFORE the provider call (Task 5) | `BudgetExceededError` (429) |
| **Guardrail blocked** | injection / failed grounding or schema (Task 8) | `GuardrailViolationError` (422) |

408 (Request Timeout) is a 4xx but semantically a timeout, so it is retryable
alongside 429 — not rejected.

## Resilience (`adapters/litellm_gateway.py`)

- **Resolution**: explicit `model` → org `model_configs` row → Settings default; the
  fallback chain likewise; order-preserving de-dupe.
- **Per attempt**: explicit timeout; bounded retry (`llm_max_retries`) with exponential
  backoff + full jitter on retryable errors; a rejection surfaces immediately.
- **Fallback**: a model's exhaustion advances the chain; the chain's exhaustion →
  `UpstreamUnavailableError`.
- **Circuit breaker** (`adapters/circuit_breaker.py`): keyed **per provider-model**, so
  one dead model never opens its siblings' circuits or blocks the rest of the chain.
  `failure_threshold` consecutive failures → OPEN (skipped, no attempt) for
  `reset_seconds` → HALF_OPEN single probe → CLOSED (success) or re-OPEN (failure).
  In-process state with an injected clock — no module-level globals (patterns.md §10).
- **Structured output**: JSON mode → validate → one bounded re-ask → typed rejection.

### Wall-clock bound (the "never hangs" guarantee)

`complete()` terminates within a computed, config-complete ceiling — both attempt
timeouts **and** inter-retry backoff are counted, so the guarantee survives any future
backoff/timeout change:

```
worst_case_wall_clock_seconds(chain_length) =
    chain_length × (retries + 1) × timeout
  + chain_length × Σ_{i=0..retries-1} min(backoff_max, backoff_base × 2^i)
```

The first term is the attempt timeouts; the second is the full-jitter ceiling of the
`retries` backoffs per model (full jitter draws in `[0, ceiling]`, so the ceiling is the
bound). The all-providers-down drill asserts actual termination is under this value.
