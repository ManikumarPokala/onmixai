# OnMixAI — CLAUDE.md (Final, v2)

This file is the authoritative engineering contract for this repository and the entry point for Claude Code. Read it fully before any change. When any rule here conflicts with convenience or speed, the rule wins. Deviations require explicit justification in the PR description.

---

## 0. Project Documents & Execution Order

| File | Purpose | Authority |
|---|---|---|
| `CLAUDE.md` (this file) | Engineering contract — rules, layering, quality gates | Binding on every change |
| `docs/prd.md` | Product requirements (OnMixAI PRD v2.0) — WHAT to build | Source of truth for scope |
| `docs/patterns.md` | Canonical logic shapes — service anatomy, state machines, pipelines, workers | Copy these shapes; never invent new ones |
| `docs/performance.md` | Time/space complexity rules, hot-path budgets, benchmarks | Binding on every PR |
| `docs/roadmap.md` | Phased plan (Phase 0 → 7 → V1 GA) | Phases execute strictly in order |
| `SPRINT-1.md` | Phase 0 execution spec, task-by-task with VERIFY gates | Current active spec |

Execution protocol:
1. Work only on the current phase's sprint spec. Complete tasks in order; run every VERIFY block; never proceed on red.
2. Before writing any code: read the target domain's `README.md`, `service.py`, and tests; match existing patterns exactly.
3. Phase gates are binary — a phase is closed only when all exit criteria are green.
4. Mid-phase feature ideas are recorded in `docs/backlog.md`, not built.

## 1. Prime Directives

1. No temporary workarounds, hardcoded values, TODO stubs, or "fix later" code on main. If it isn't production-quality, it doesn't merge.
2. New code must look like it was written by the same team that wrote the old code — same naming, error handling, test style. Never introduce a second way of doing something that has an established way.
3. Prefer boring, proven solutions. Optimize for the engineer reading this in 12 months.
4. Every feature ships with tests, error handling, logging, complexity annotations, and documentation — these are the feature, not follow-ups.
5. When a requirement is ambiguous, stop and ask one precise question. Never guess, never invent endpoints, schema fields, or config values.

## 2. Repository Layout

```
onmixai/
├── backend/
│   ├── src/
│   │   ├── identity/  knowledge/  search/  conversation/  ai/
│   │   ├── agents/  recommendation/  reports/  governance/  admin/
│   │   └── shared/            # cross-cutting only (config, db, errors, logging, security)
│   ├── tests/                 # mirrors src/; + tests/isolation/ + tests/benchmarks/
│   ├── alembic/  pyproject.toml  Dockerfile
├── frontend/src/{features,components,lib,app}/
├── infra/  docs/{adr,runbooks}/  .github/workflows/
```

## 3. Backend Architecture Rules

### 3.1 Layering inside every domain

```
<domain>/router.py        # HTTP only. Thin: validate → one service call → shape response. ~15 lines/fn.
<domain>/service.py       # Business logic. No HTTP objects, no raw SQL.
<domain>/repository.py    # ALL queries live here. No business decisions.
<domain>/rules.py         # Pure business-rule functions. Zero I/O.
<domain>/models.py  schemas.py  exceptions.py  dependencies.py
```

Hard rules: router → service → repository, no layer skipping. Services never import SQLAlchemy query constructs. Constructor injection only — a service that builds its own dependencies does not merge.

### 3.2 Canonical service method (patterns.md §1 — follow exactly)

Order is fixed: **1. AUTHORIZE → 2. LOAD (tenant-scoped) → 3. CHECK INVARIANTS (pure rules) → 4. MUTATE → 5. RECORD (audit) → 6. RETURN DTO.** One method = one use case = one transaction (request scope owns commit/rollback). Steps 1–3 complete before any mutation. >40 lines or two use cases → split.

### 3.3 Cross-domain communication

Domain A calls Domain B only through B's service interface — never B's repository or models. Dependency directions live in `docs/adr/0002`; circular dependencies are a design error — stop and raise. Enforced by `import-linter` in CI.

### 3.4 State machines

Any entity with a lifecycle gets an explicit transition map and a single `transition()` function (patterns.md §3). Status changes in the DB use compare-and-set (`UPDATE ... WHERE status = :expected`) so concurrent workers can never both claim the same row.

### 3.5 Pipelines and agents

Multi-step AI flows (RAG, LangGraph) are composed, individually-testable steps with typed immutable intermediates (patterns.md §5). Refusal/degraded outcomes are typed first-class results, not exceptions.

### 3.6 External integrations

Every external system (LLM, embeddings, OCR, storage) sits behind a Protocol we own, with one adapter per provider and one deterministic fake for tests. Provider SDK imports exist ONLY in `adapters/` (import-linter contract). Timeouts, bounded retry with backoff+jitter, fallback chains, circuit breakers, metering, and tracing live in the adapter once — features cannot bypass the gateway.

### 3.7 Workers

Idempotent by construction (patterns.md §7): atomic claim via compare-and-set, deterministic upserts (content hash), bounded retries, always a user-visible terminal state (FAILED + reason), sweeper for tasks orphaned by dead workers. CPU-bound work never runs on the event loop.

### 3.8 Configuration

One typed `pydantic-settings` class; fail fast at startup naming the bad variable. No `os.getenv` elsewhere. No magic numbers — timeouts, limits, model names live in config. No secrets in code or fixtures; `.env.example` documents every variable.

## 4. Multi-Tenancy & Security — Non-Negotiable

- Every tenant-owned table: `org_id NOT NULL` indexed; Postgres RLS policies created **in the same migration** as the table, with `FORCE ROW LEVEL SECURITY`; runtime DB role is non-superuser, non-bypassrls.
- Defense in depth: application code ALSO scopes every query by org_id. A repository method touching tenant data without tenant context as a parameter must not exist.
- Vector search filters by org_id + collection ACLs **inside the SQL predicate, before similarity ranking**. Retrieval without an ACL filter is a security bug.
- Tenant-isolation test suite (two orgs, zero cross-tenant reads through every public method, IDOR attempts, raw-count RLS proof) is a separate, permanently blocking CI job. It can never be skipped or reduced.
- JWT: short-lived access, rotating refresh (hash stored, never raw); reuse of a revoked refresh token revokes ALL tokens for that user. Identical errors for wrong-email vs wrong-password (no enumeration). Rate limiting on auth endpoints.
- All user content reaching a prompt passes the guardrail chain: injection filtering, configurable PII redaction, grounding enforcement (cite or refuse), output schema validation.
- All timestamps UTC timezone-aware; naive datetimes banned.

## 5. Error Handling

- Typed domain errors inherit `AppError(code, status, message, detail)`. One global handler → consistent envelope: `{"error":{"code","message","request_id"}}`. Clients never see stack traces, SQL, provider bodies, or internal paths.
- No bare `except:`; no `except Exception: pass`. Catch the narrowest exception you can act on; otherwise propagate to the global handler.
- Decision table (patterns.md §9): caller's fault → typed 4xx; retryable upstream failure → adapter retries then `UpstreamUnavailableError` (503); our bug → propagate (500 + ERROR log w/ traceback server-side); expected business outcome (low confidence, empty results) → typed result, NOT an exception. Never catch-and-continue with a default that hides failure. Never return fabricated output on failure.

## 6. Logging, Tracing, Metering

- `structlog` JSON only; `print()` banned (ruff T201). `request_id` middleware on every request, propagated to workers and traces, echoed in `X-Request-ID`.
- Every LLM call traced (Langfuse): prompt template + version, model, tokens, latency, retrieved source IDs — wired once in the gateway adapter.
- Token metering + per-org budgets (soft warn / hard cap) enforced in the gateway, nowhere else.
- Expected 4xx logged at INFO; 5xx at ERROR with full context (request_id, org_id, user_id, operation).

## 7. Database & Migrations

- Alembic only; every migration has a working `downgrade()`; never edit a merged migration. Destructive ops use the two-step deploy pattern documented in the migration docstring.
- Every query has an index that serves it. Hot queries carry plan-assertion tests (`EXPLAIN` must show index/HNSW scan; sequential scan on tenant tables fails CI).
- No N+1: explicit `selectinload`/`joinedload`; list-endpoint tests assert query counts.
- All list methods paginated with a hard server-side cap; no unbounded `SELECT *`. Bulk inserts for chunks/embeddings — never per-row awaits in loops.
- pgvector HNSW parameters configured in one place; embedding-model changes trigger the documented re-index procedure, never in-place dimension changes.

## 8. Performance & Complexity (full rules: docs/performance.md)

- Every non-trivial function declares Time/Space complexity in its docstring with n named. Wrong annotation = defect. Cannot state it = rewrite until you can. Every loop's bound must be identifiable.
- Hot-path budgets are benchmarked in CI (`tests/benchmarks/`), regressions block merge: hybrid search p95 < 3s @ 1M chunks; context assembly < 50 ms; metering check O(1) < 5 ms; chunking single-pass O(p).
- Required: set/dict membership (never list scans in loops), dict-index joins (never nested loops over unbounded collections), `heapq.nlargest` for top-k, `"".join` for string building, generators for single-pass large data.
- Banned: `pop(0)`/`insert(0)` in loops, quadratic concatenation, recursion over user-controlled depth, sorting inside loops, unbounded caches (every cache declares maxsize + eviction).
- Streaming over buffering: uploads stream to storage; ingestion processes in batches — peak memory O(batch), independent of document size. One pathological file may fail its own task, never the worker fleet.
- Independent awaits gathered with bounded concurrency (`Semaphore`); backpressure: queue depth metric + 429 load-shedding on the upload path.
- Optimization PRs include before/after measurements. Fix the algorithm class before tuning constants.

## 9. Testing Standards

- Pyramid: unit (services w/ mocked repos + branch-complete tests for every `rules.py` function), integration (real Postgres via testcontainers, RLS active), API (httpx), isolation suite, benchmarks.
- Coverage ≥ 80% on `src/` (floor, not goal) — auth, ACL filtering, budget enforcement, guardrails get explicit edge-case tests regardless.
- LLM calls never occur in unit/integration tests — `FakeGateway` via DI. Determinism mandatory.
- AI quality gated separately: golden-set evals (`make eval`) — retrieval recall@5/MRR, generation faithfulness via versioned LLM-as-judge rubric. A prompt/model change without an eval run does not merge. Golden sets only grow; past baselines are permanent regression gates.
- Every bug fix ships a regression test that fails before the fix. Test names describe behavior: `test_search_excludes_documents_user_cannot_access`.

## 10. Code Quality

**Python:** 3.12, full type hints, `mypy --strict` (any `Any` needs a justifying comment). `ruff` lint+format, zero warnings; complexity cap C901=10. Docstrings on public service/repository methods: behavior, tenant-scoping, raised exceptions, complexity (§8). No commented-out or dead code — delete it, git remembers.

**TypeScript/React:** strict mode; no `any`, no `@ts-ignore` without linked issue. Feature-sliced structure. Server state via TanStack Query; no server data mirrored into global stores. One OpenAPI-generated typed API client — no scattered `fetch`. Every async UI handles loading/empty/error(+retry)/success explicitly. Semantic HTML, keyboard navigation, labeled inputs.

**Schemas:** three layers never mixed — request/response schemas (allow-lists; sensitive fields structurally absent), internal DTOs, ORM models. Conversions centralized as `Schema.from_model()`. No dict-shaped data across layer boundaries.

## 11. Git, Review, CI

- Trunk-based; branches `feat/<domain>-<slug>` / `fix/<domain>-<slug>`; Conventional Commits; small single-purpose PRs (<~400 lines); PR description: what/why/how-tested/deviations.
- CI gates (all blocking, no force-merging, no skipped hooks):
  1. ruff check + format  2. mypy --strict  3. import-linter contracts
  4. tests + coverage ≥80%  5. tenant-isolation suite (separate named job)
  6. migration upgrade→downgrade→upgrade on clean DB  7. benchmarks within budget
  8. frontend: tsc, eslint, tests, build  9. gitleaks + pip-audit/npm audit (high+)
  10. golden-set eval — required when `src/ai/prompts/` or model config changes

## 12. Documentation

- ADRs in `docs/adr/` for every significant design choice (context → decision → consequences); binding until superseded.
- Each domain has a `README.md`: responsibility, public service interface, invariants, known limitations.
- Runbooks in `docs/runbooks/`: re-indexing, provider outage, backup restore, secret rotation, connection exhaustion.

## 13. Definition of Done (every feature, no exceptions)

- [ ] Layering + domain-dependency rules followed; service methods follow the 6-step anatomy, ≤~40 lines
- [ ] Business decisions in `rules.py` as pure functions with branch-complete tests
- [ ] Lifecycle changes via transition map; concurrent paths compare-and-set
- [ ] Tenant scoping verified; isolation suite extended if new tenant data introduced
- [ ] Errors per decision table; no bare excepts; expected outcomes typed, not exceptions
- [ ] Structured logging with request context on new paths
- [ ] External calls behind Protocols via the gateway; traced; fake used in tests
- [ ] Complexity annotations present and correct; no banned shapes (§8, patterns.md §10); loops bounded
- [ ] Hot-path changes include benchmark results within budget
- [ ] New queries indexed; no N+1 (query-count test); lists paginated and capped
- [ ] Migrations reversible and CI-tested
- [ ] Unit + integration tests; coverage gate passes; LLM features have eval coverage
- [ ] Frontend states (loading/empty/error/success) implemented
- [ ] Domain README / ADR updated if behavior or design changed
- [ ] Zero TODOs, commented-out code, hardcoded config, or secrets in the diff

## 14. Claude Code Operating Protocol

1. **Read before writing.** Inspect the domain's README, service, schemas, and tests. Trace dependencies. Understand before modifying.
2. **Copy canonical shapes** from `docs/patterns.md` — never invent a new structure for something that has one.
3. **Follow the active sprint spec** task-by-task; run every VERIFY block; commit per task with Conventional Commits; never proceed on red.
4. **Source of truth discipline:** scope from the PRD, shapes from patterns.md, budgets from performance.md, order from roadmap.md. Never invent endpoints, fields, or config.
5. **Architectural changes → ADR first**, implementation second.
6. **Ambiguity → one precise question.** Guessing is more expensive than asking.
7. **Self-review against §13** before declaring any task complete. A task is done when the checklist is fully true — not when the code runs once.
