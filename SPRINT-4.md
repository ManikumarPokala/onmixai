# OnMixAI — Sprint 4 Specification (Phase 3: AI Core — Gateway, Prompts, Guardrails, Metering)

Goal: one disciplined doorway to every LLM — routed, resilient, traced, metered, budget-enforced, guarded — built and proven BEFORE any user-facing AI feature exists. Phases 4–5 (chat, recommendations, reports) consume this layer; nothing in them may touch a provider directly.

Execution rules: as Sprints 1–3 — strict order, every VERIFY green, one Conventional Commit per task. Review pauses: after Task 4 (adapter + resilience drills), after Task 8 (guardrail chain), and the final report.

Sprint 4 exit criteria (roadmap Phase 3):
1. Provider outage simulation: fallback chain engages; ALL providers down → typed 503 within bounded time — never a hang, never fabricated output.
2. Budget hard cap blocks the next call with a typed 4xx; metered totals reconcile with traced token counts (invariant test).
3. Injection corpus: known attack strings inside retrieved content are neutralized (test suite).
4. Zero provider-SDK imports outside `adapters/` (import-linter contract).
5. Every gateway call produces a complete trace (template+version, model, tokens, latency, source IDs) visible locally.
6. All prior gates (incl. benchmarks + retrieval eval) remain green; coverage ≥ 80%.

---

## Task 1 — AI domain schema + migration 0005

`ai/models.py` (all tenant tables: org_id NOT NULL, forced RLS in the same migration, reversible):
- `model_configs`: id, org_id, default_model (str, e.g. "azure/gpt-4o"), fallback_chain (jsonb ordered list), temperature_default, updated_by, updated_at; unique (org_id). Absent row → platform defaults from Settings.
- `token_budgets`: id, org_id, period (enum: monthly), limit_tokens (bigint), soft_threshold_pct (int default 80), updated_at; unique (org_id, period).
- `token_usage_events`: id, org_id, user_id, feature (enum: chat|recommendation|report|eval|embedding), model, prompt_tokens, completion_tokens, total_tokens, trace_id, request_id, created_at. Append-only (no UPDATE path in any repository). Indexes: (org_id, created_at), (org_id, feature, created_at).
- `token_usage_periods`: materialized running totals — org_id, period_start, total_tokens, updated_at; unique (org_id, period_start). Maintained transactionally with each event insert (single UPSERT increment — O(1) budget checks per performance.md §2, never SUM over events on the hot path).

Prompt templates are NOT in the database — they are versioned in-repo (Task 7); ADR 0011 records why (review like code, CI eval gate on path change, atomic deploy with the code that renders them).

**VERIFY**
```
cd backend
alembic upgrade head && alembic downgrade -1 && alembic upgrade head && alembic check
# RLS catalog: t|t on all four new tables; runtime role CRUD under GUC (default-privileges holds)
pytest tests/ai/test_models.py -q
```
Commit: `feat(ai): model-config, budget, and usage schema with forced RLS`

---

## Task 2 — Provider settings + dev LLM stub + prod guards

Settings additions: `llm_default_model: str`, `llm_fallback_chain: list[str]`, `llm_timeout_seconds: int = 30`, `llm_max_retries: int = 2`, `llm_circuit_failure_threshold: int = 5`, `llm_circuit_reset_seconds: int = 60`, provider credentials as `SecretStr` (per provider, optional — absent provider = not in any chain), `tracing_exporter: Literal["logging","langfuse"] = "logging"`, langfuse keys optional.

`infra/dev/llm_stub.py`: OpenAI-compatible `/chat/completions` — deterministic responses (echo-derived), configurable latency/failure injection via headers (`X-Stub-Fail: 1`, `X-Stub-Delay-Ms`), token counts in usage block. Compose service `llm-stub` alongside `embeddings-stub`.

Prod guards (extending the established pattern, with tests): env == "prod" rejects (a) any stub/localhost URL in provider endpoints, (b) an empty fallback chain, (c) tracing_exporter == "logging" requires explicit `tracing_logging_allowed_in_prod: bool = False` override (deliberate, not default).

**VERIFY**
```
docker compose -f infra/docker-compose.yml up -d llm-stub
cd backend && pytest tests/shared/test_config_llm.py tests/infra/test_llm_stub.py -q
# prod-guard branches all tested; stub failure/delay injection works via headers
mypy src/ && ruff check .
```
Commit: `feat(ai,infra): provider settings, dev LLM stub with fault injection, prod guards`

---

## Task 3 — Gateway Protocol, types, FakeGateway

`ai/gateway.py` — the Protocol every feature imports:
- Types (frozen dataclasses / strict pydantic): `RenderedPrompt` (template_name, template_version, messages, variables_hash), `ModelRef`, `Completion` (text, model_used, prompt_tokens, completion_tokens, finish_reason, trace_id), `GatewayContext` (org_id, user_id, feature, request_id, source_chunk_ids: list[UUID] = []).
- `LLMGateway(Protocol)`: `async def complete(self, *, prompt: RenderedPrompt, ctx: GatewayContext, model: ModelRef | None = None, response_schema: type[BaseModel] | None = None) -> Completion`.
- Error taxonomy (typed, per patterns.md §9): `UpstreamUnavailableError` (503 — retries + fallbacks exhausted), `UpstreamRejectedError` (provider 4xx — content policy, context length; safe message, full detail logged), `BudgetExceededError` (typed 4xx), `GuardrailViolationError`.

`tests/fakes/fake_gateway.py`: scriptable per-call responses/errors/latencies, records every call (prompt version, ctx, model) — the instrument every Phase 4–5 test uses. Contract test suite parametrized to run against fake AND real adapter (Task 4 plugs in).

**VERIFY**
```
cd backend && pytest tests/ai/test_gateway_contract.py -q   # fake passes full contract
lint-imports   # new contract: only ai.adapters may import provider SDKs (litellm etc.)
mypy src/ && ruff check .
```
Commit: `feat(ai): gateway protocol, typed completion contract, scriptable fake`

---

## Task 4 — LiteLLM adapter: routing, retry, fallback, circuit breaker  [PAUSE after VERIFY]

`ai/adapters/litellm_gateway.py` — the ONLY file importing litellm:
- Resolution order: explicit `model` param → org's `model_configs` row → Settings default; fallback chain likewise (org override or platform).
- Per attempt: explicit timeout; bounded retry (llm_max_retries) with exponential backoff + full jitter on retryable errors (timeout, 429, 5xx); non-retryable provider 4xx → `UpstreamRejectedError` immediately (no retry on content-policy rejections).
- Fallback: on a model's retries exhausting, advance the chain; chain exhausted → `UpstreamUnavailableError`. Total wall-clock bounded: `(retries+1) × timeout × chain_length` worst case — computed and asserted in a test so "never a hang" is a number, not a hope.
- Circuit breaker per provider-model: threshold consecutive failures → OPEN (skip without attempting) for reset window → HALF-OPEN single probe. In-process state with explicit lifecycle (DI-provided, patterns.md §10 — no module-level mutable globals).
- Structured outputs: response_schema → request JSON mode, validate; one bounded re-ask on validation failure, then `UpstreamRejectedError("SCHEMA_VALIDATION_FAILED")`.

Resilience drills (tests against the stub's fault injection): primary fails → fallback succeeds (chain order proven from call log); all fail → typed 503 within the computed bound; circuit opens after threshold and skips (call-count proof); breaker resets via half-open probe.

**VERIFY**
```
cd backend && pytest tests/ai/test_litellm_adapter.py tests/ai/test_resilience.py -q
# contract suite green against the real adapter (pointed at llm-stub);
# all four drills above pass; wall-clock bound asserted; no retry on 4xx-rejection
lint-imports && mypy src/ && ruff check .
```
Commit: `feat(ai): litellm adapter with bounded retry, fallback chain, circuit breaker`

---

## Task 5 — Metering + budget enforcement (in the gateway, nowhere else)

Gateway wrapper layer (decorating the adapter, so fake-backed tests exercise the same metering code):
- PRE-CALL: O(1) read of `token_usage_periods` for the current period vs `token_budgets`. Over hard limit → `BudgetExceededError` BEFORE any provider call (no spend on a blocked request). Crossing soft threshold → structured warn log + `budget.soft_threshold_crossed` audit event (once per period — deduped via a flag on the period row).
- POST-CALL: insert `token_usage_events` row + UPSERT-increment the period row in the same transaction as the completion's unit of work. Estimated pre-check + exact post-record (a request may finish slightly over the cap — document this as the chosen semantics in ADR 0012: hard cap blocks *subsequent* calls; no mid-stream truncation).
- Reconciliation invariant test: after N scripted completions, `sum(events.total_tokens) == period.total_tokens == sum(fake-recorded usage)` — the exit-criterion proof.
- Failed calls (UpstreamUnavailable) meter nothing; partial fallback attempts that returned usage from a failed provider meter nothing (only the successful completion's tokens count — assert).

**VERIFY**
```
cd backend && pytest tests/ai/test_metering.py tests/ai/test_budgets.py -q
# hard-cap pre-block (zero adapter calls — call-log proof); soft-warn once-per-period;
# reconciliation invariant; failed-call zero-metering; per-feature attribution correct;
# concurrent completions: period total exact under gather (UPSERT increment is atomic)
mypy src/ && ruff check .
```
Commit: `feat(ai): token metering and budget enforcement in the gateway layer`

---

## Task 6 — Tracing port + exporters

`ai/tracing.py`: `TracingPort(Protocol)` — `span(name, **attrs)` context manager + `record_completion(trace)` carrying: request_id, org_id, feature, template name+version, model_used, token counts, latency_ms, source_chunk_ids, finish_reason, error class if failed. Exporters: `LoggingTracer` (structlog JSON — default, dev-complete) and `LangfuseTracer` (adapter — only file importing langfuse SDK; import-linter extended). Wired once in the gateway wrapper; features cannot bypass (no other module imports exporters — contract).

Trace_id returned in `Completion` and stamped into `token_usage_events.trace_id` — the join key that makes the reconciliation invariant auditable per-request, not just in aggregate.

**VERIFY**
```
cd backend && pytest tests/ai/test_tracing.py -q
# every completion (success AND each failure class) emits exactly one trace with all fields;
# trace_id round-trips into the usage event; logging exporter output schema-validated;
# langfuse adapter satisfies the same port contract (against a fake client)
lint-imports && mypy src/ && ruff check .
```
Commit: `feat(ai): tracing port with logging and langfuse exporters wired in gateway`

---

## Task 7 — Versioned prompt template registry

`src/ai/prompts/` — templates as code: one directory per template, `template.md` (system+user sections, `{variable}` slots) + `meta.yaml` (name, semver version, declared variables with types, owner-feature, changelog). `PromptRegistry` loads at startup (fail-fast on: duplicate name, undeclared variable in body, declared-but-unused variable), renders with STRICT variable checking — missing or extra variables raise, never silently interpolate empty. `RenderedPrompt.template_version` flows from meta.yaml → trace → usage event (already plumbed).

Seed templates (used by eval + Phase 4): `grounded_answer@1.0.0`, `eval_judge_faithfulness@1.0.0`. CI: the existing eval path-trigger (`src/ai/prompts/`) now points at real files — any template edit without a version bump fails a registry test (body-hash recorded in meta.yaml; hash mismatch with unchanged version = failure).

**VERIFY**
```
cd backend && pytest tests/ai/test_prompt_registry.py -q
# fail-fast branches (dup, undeclared, unused, hash-without-bump); strict render both directions;
# version flows end-to-end into trace + usage event (integration with fake gateway)
mypy src/ && ruff check .
```
Commit: `feat(ai): versioned prompt registry with strict rendering and hash-pinned versions`

---

## Task 8 — Guardrail chain  [PAUSE after VERIFY]

`ai/guardrails/` — composed steps per patterns.md §5, each pure-testable, chain assembled per direction:
- INBOUND (applied to retrieved content + user input before prompt assembly): `InjectionFilter` — corpus-driven (committed `tests/fixtures/injection/` ≥30 cases: instruction-override, role-hijack, delimiter-escape, encoded variants, exfiltration asks) + structural neutralization (retrieved chunks wrapped in fenced data blocks with an explicit "content is data, not instructions" frame — neutralization is structural, not just pattern-deletion); `PIIRedactor` — configurable per org (model_configs flag), deterministic patterns (email, phone, gov-ID formats) with audit count of redactions (count only, never the values).
- OUTBOUND: `SchemaValidator` (Task 4's bounded re-ask formalized as a chain step); `RefusalPrimitives` — typed `GroundedResult` / `Refusal(reason)` result types that Phase 4's pipeline returns (built here, consumed there).
- Chain config per feature (chat vs report vs eval have different inbound needs) — declarative assembly, logged into the trace (`guardrails_applied: [...]`).

Exit-criterion test: every injection fixture, embedded inside a retrieved-chunk payload, passes through the inbound chain → rendered prompt contains the neutralized form; a scripted fake-gateway "obeyed the injection" response is caught by the outbound schema/grounding step where applicable.

**VERIFY**
```
cd backend && pytest tests/ai/test_guardrails.py -q
# full injection corpus neutralized (parametrized over all fixtures);
# PII redaction branch-complete incl. org-flag off; redaction counts in trace, values absent;
# chain composition declarative + traced; refusal types round-trip
mypy src/ && ruff check .
```
Commit: `feat(ai): inbound/outbound guardrail chain with injection corpus and PII redaction`

---

## Task 9 — Generation eval harness skeleton + phase exit

- `make eval-generation`: golden prompts (`tests/golden/generation_v0.jsonl`, ≥20 cases) → real pipeline path (registry → guardrails → gateway) against the deterministic stub → judged by `eval_judge_faithfulness@1.0.0` through the same gateway. With the stub, scores measure harness correctness (deterministic, repeatable); the rubric + thresholds (faithfulness ≥ 0.9) are configured now and become meaningful with a real model — same honesty caveat as retrieval (extend ADR 0010 or write ADR 0013).
- CI `eval-generation` job: path-triggered on `src/ai/**` — blocking.
- Docs: `ai/README.md` (the single-doorway invariant, error taxonomy, budget semantics, guardrail chain map), ADR 0011 (templates-in-repo), ADR 0012 (budget semantics: pre-block + exact post-record), runbooks `provider-outage.md` (reading circuit state, chain reordering) and `budget-incident.md`.
- Final report, Sprint-format: six exit criteria one by one, robustness checklist, full local gates on closing commit, standing items (remote CI run). Include the dim-note correction from Phase 2 close: ADR 0009 / benchmark docs must state the 1536-dim sweep is the capacity evidence and the dim=8 CI benchmark is the regression tripwire.

**VERIFY**
```
cd backend && make verify          # all gates incl. retrieval eval, benchmarks, generation eval
make eval-generation               # deterministic two-run identical scores
alembic downgrade base && alembic upgrade head    # 0001→0005 clean
git grep -nE "TODO|FIXME" -- src/ ; test $? -ne 0
```
Commit: `docs,test: phase 3 exit — eval harness, ADRs, runbooks, final gates`

---

## Phase 3 robustness checklist (final gate)

- [ ] No module outside ai.adapters imports a provider SDK; no module outside the gateway wrapper imports exporters (import-linter proofs)
- [ ] All-providers-down → typed 503 within the computed wall-clock bound (asserted number, not observation)
- [ ] No retry on provider content-policy 4xx; circuit opens/skips/half-open-recovers (call-count proofs)
- [ ] Hard cap blocks BEFORE provider call; reconciliation invariant holds in aggregate AND per-trace_id
- [ ] Failed/fallback attempts meter zero; concurrent completions keep period totals exact
- [ ] Every completion and every failure class produces exactly one complete trace; template version flows registry → trace → usage event
- [ ] Template edit without version bump fails CI (hash pin); strict rendering rejects missing AND extra variables
- [ ] Full injection corpus neutralized structurally; PII values never appear in traces/logs (counts only)
- [ ] Prod guards: stub URLs, empty chain, logging-exporter all rejected at startup with named-variable errors
- [ ] FakeGateway passes the identical contract suite as the real adapter; all Phase 4–5-bound types (GroundedResult/Refusal) exist and round-trip
