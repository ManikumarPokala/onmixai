# Changelog

All notable changes to OnMixAI. Format follows [Keep a Changelog](https://keepachangelog.com);
this project uses [Conventional Commits](https://www.conventionalcommits.org).

## [1.0.0] — unreleased (V1 GA candidate)

First generally-available release: a multi-tenant RAG platform that gives **grounded, cited answers
or an explicit refusal** — never a confident guess — with hard tenant isolation, an immutable audit
trail, and per-org governance.

### Added — by domain

- **Identity & tenancy** — orgs/users, JWT access + rotating refresh (reuse revokes all tokens),
  RBAC, rate limiting, no user-enumeration. Postgres **forced RLS** on every tenant table behind a
  non-superuser, non-bypassrls runtime role.
- **Knowledge** — streaming uploads, idempotent parse→chunk→embed ingestion (content-hash dedup),
  document lifecycle state machine, collection ACLs, storage-deletion compensation outbox.
- **Search** — hybrid retrieval (HNSW vector + Postgres FTS, reciprocal-rank fusion) with the
  org + collection ACL filter applied **inside the SQL predicate before ranking**; plan-asserted
  (no seq scan on tenant tables); recall@5 golden gate.
- **Conversation** — grounded streaming chat: **cite-or-refuse**, terminal grounding validation
  (ADR 0014), low-confidence refusal before any spend, rolling summaries, feedback.
- **AI gateway** — provider-agnostic gateway (litellm) with bounded retry, fallback chain, circuit
  breaker, token metering + per-org budgets, Langfuse tracing, guardrails (injection, PII, schema).
  **Azure OpenAI** support (`azure/<deployment>` routing, fail-fast config) — Azure-ready,
  verify-against-your-deployment via `scripts/azure_smoke.py`.
- **Recommendation & Reports** — decision outputs with confidence bands derived from retrieval
  (never model self-report), declines on insufficient evidence, LangGraph report pipeline, PDF export.
- **Governance & Admin** — immutable `audit_events` (UPDATE-reject trigger + REVOKE + a dedicated
  least-privilege purger role); usage analytics; user/org/AI-config/budget administration;
  knowledge-base administration; declarative retention policy + a crash-resumable, audit-before-delete
  retention purge (retain-by-default); feedback→golden curation (PII-redacted, human-gated); a
  per-org PII-redaction toggle decoupled from telemetry; a React admin console with
  consequence-confirmed destructive actions.

### Security
- JWT-secret **rotation grace window** (dual-secret verification) so live sessions survive rotation.
- OWASP top-10 review (control → proving test); `pip-audit` / `npm audit` clean; gitleaks full-history.
- Audit-coverage **enumeration test**: every mutating service method across all six domains emits an
  audit event, enforced in CI (fails on any gap).

### Quality & resilience
- Tenant-isolation suite (permanently blocking), branch-complete rule tests, golden-set evals
  (retrieval 150 / chat 75 + generation/recommendation/report), hot-path benchmarks.
- Failure drills (DB restart, provider outage, worker death, storage failure, retention crash, +
  a compound mid-stream fault) and a backup/restore + DR drill — automatic recovery, zero data loss.

### Engineering
- DDD layering enforced by import-linter; `mypy --strict`; ruff; reversible Alembic migrations
  (CI-tested up→down→up); typed fail-fast config; structured logging with request-id propagation.

### Notes / standing items (honestly stated)
- **Real-model eval** and **live Azure verification** are user-executed (CI uses a deterministic
  stub; the agent never calls a live provider). Eval numbers are pipeline-correctness; see
  `docs/benchmarks/v1_quality_baseline.md` for the real-model re-baseline procedure.
- **Load / failure / backup-restore drills** are user-run against a live stack; evidence is recorded
  in `docs/benchmarks/` and `docs/runbooks/` from those runs.
- See `DECISIONS.md` for the engineering decision log and `DEMO.md` for the 30-second demo.
