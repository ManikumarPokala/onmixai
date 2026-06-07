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

## Metering & budgets (`metering.py`, ADR 0012)

`MeteringGateway` decorates the gateway so token counting and budgets live in exactly
one place. **Pre-call:** an O(1) read of the materialized period total vs the org budget;
over the hard limit → `BudgetExceededError` (429) *before any provider call*. **Post-call:**
an immutable `token_usage_events` row + an atomic UPSERT-increment of the period total,
in the request's transaction. **Semantics:** the cap blocks *subsequent* calls — an
in-flight call finishes and is recorded exactly (a request may push slightly over; the
next is blocked; no mid-stream truncation). Failed/fallback calls meter nothing. The
reconciliation invariant `Σ events == period total == Σ provider usage` holds in
aggregate and per `trace_id`. Soft-threshold crossings warn + audit once per period
(compare-and-set).

## Tracing (`tracing.py`)

`TracingGateway` emits exactly one trace per call — success and each typed failure —
carrying template name+version, model, token counts, latency, `source_chunk_ids`,
`finish_reason`, and error class. `trace_id` is the join key to the usage event.
Exporters: `LoggingTracer` (structlog, dev) and `LangfuseTracer` (the only langfuse-
importing module). The full stack is composed once: **tracing → metering → adapter**
(`build_metered_traced_gateway`) — features cannot bypass any layer.

## Guardrails (`guardrails/`, CLAUDE.md §4)

Composed steps, assembled declaratively **per feature**:

| Direction | Step | What |
|---|---|---|
| inbound | `PIIRedactor` | per-org opt-in; email/gov-id/phone → `[REDACTED_*]`; returns **counts only** (values never logged/traced) |
| inbound | `InjectionFilter` | structural: wrap retrieved content in **nonce-delimited** `<<UNTRUSTED_DATA_{nonce}>>` markers + a "data, not instructions" frame; forging the close marker is impossible (nonce) and literal marker tokens are escaped (defense in depth) |
| outbound | `OutboundGuardrails.validate_structured` | a response that ignored the required schema (obeyed an injection) → `GuardrailViolationError` → `Refusal` |

Per-feature chains: chat / report / recommendation = `(pii_redactor, injection_filter)`;
**eval = `(injection_filter,)`**. `guardrails_applied` + `redaction_counts` are logged
into the trace (counts only).

### Eval-chain PII exemption — a constraint, not a gap

The eval feature **skips PII redaction** on purpose: the faithfulness judge must score
the *real* answer, and redacting it would corrupt the judgment. This is only safe because
**eval inputs are synthetic or pre-redacted** — golden sets and the deterministic stub
(ADR 0013), never live user content. **Therefore: eval traffic must never carry real user
PII to an external provider.** Any future "eval against production transcripts" idea must
pre-redact at the source (or run against a self-hosted model); the exemption is enforced
by *where eval data comes from*, and must stay that way.

