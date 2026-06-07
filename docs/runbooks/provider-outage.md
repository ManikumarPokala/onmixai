# Runbook — LLM provider outage

Symptoms: AI features return `503 UPSTREAM_UNAVAILABLE`, latency climbs, or traces show
repeated retries/fallbacks. The gateway never hangs — worst-case wall clock is bounded
(ADR 0009-style bound: `chain × (retries+1) × timeout + backoff ceiling`) and an
all-down chain returns a typed 503, never fabricated output.

## Quick triage

1. **Read the traces.** Every completion (success and each failure) emits one trace
   (`ai.completion` / `ai.completion.error`) with `model_used`, `latency_ms`,
   `finish_reason`, and the `error` class. A burst of `UpstreamUnavailableError` with
   rising `latency_ms` = a provider is degraded; `UpstreamRejectedError` = content/
   context rejections (not an outage).
2. **Which provider?** Group failing traces by `model_used`. One model failing while
   others succeed is a single-provider issue; all failing is a network/credentials
   problem.
3. **Circuit state.** A provider-model that crossed `llm_circuit_failure_threshold`
   consecutive failures is OPEN and is being **skipped** (no attempt) until
   `llm_circuit_reset_seconds` elapses, then a single HALF-OPEN probe decides. An open
   circuit is the breaker working — traffic is already shedding to the fallback chain.

## Causes and actions

- **One provider down, chain absorbs it** → no action needed; the fallback chain is
  engaging (trace shows the primary failing then the fallback succeeding). Confirm the
  fallback model is healthy and has budget headroom.
- **Reorder / extend the chain** → the chain is per-org (`model_configs.fallback_chain`)
  or the platform default (`LLM_FALLBACK_CHAIN`). To prefer a healthy provider during an
  outage, move it earlier in the chain (org override for one tenant, or the platform
  default for all). Changes take effect on the next request — no restart.
- **All providers down → typed 503** → expected, bounded behavior; the gateway is
  refusing, not hanging or inventing answers. Restore a provider or add a healthy one to
  the chain. Verify `LLM_BASE_URL`/keys are correct (a stub/localhost URL in prod is
  rejected at startup by a prod guard).
- **Circuit stuck OPEN after the provider recovers** → it self-heals: after the reset
  window a HALF-OPEN probe closes it on the first success. To force a faster recovery,
  lower `LLM_CIRCUIT_RESET_SECONDS` (next deploy) — the breaker is in-process, so a
  rolling restart also resets all circuits.
- **Retries amplifying load on a rate-limited provider** → `429` is retryable with
  backoff; if a provider is rate-limiting under load, reduce `LLM_MAX_RETRIES` or move
  it down the chain so retries don't pile onto the struggling provider.

## Verifying recovery

- New traces show `ai.completion` (no `error`) at normal `latency_ms`; the previously
  open circuit is CLOSED (a success was recorded for that model).
- No fabricated output occurred during the outage — failed calls returned typed errors
  and **metered nothing** (the reconciliation invariant still holds; ADR 0012).

## Knobs (Settings / env)

`LLM_FALLBACK_CHAIN`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`,
`LLM_BACKOFF_BASE_SECONDS` / `LLM_BACKOFF_MAX_SECONDS`,
`LLM_CIRCUIT_FAILURE_THRESHOLD` / `LLM_CIRCUIT_RESET_SECONDS`. Per-org overrides live in
`model_configs` (default model + fallback chain).
