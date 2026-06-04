# OnMixAI — Phased Development Roadmap (docs/roadmap.md)

Execution model: solo developer + Claude Code. Phases are strictly ordered — each phase builds only on completed, verified phases. A phase is closed only when every exit criterion is green; no phase starts on partial credit from the previous one.

Every phase inherits the standing gates: CLAUDE.md compliance, patterns.md shapes, full CI green, tenant-isolation suite green, coverage ≥ 80%.

Timeline assumes ~30 focused hrs/week. Durations are realistic, not optimistic.

```
Phase 0  Foundation                 Weeks 1–2     (SPRINT-1.md — specced)
Phase 1  Knowledge / Ingestion      Weeks 3–5
Phase 2  Search / Retrieval         Weeks 6–7
Phase 3  AI Core / Gateway          Weeks 8–10
Phase 4  Conversation / Chat        Weeks 11–12
Phase 5  Recommendations + Reports  Weeks 13–15
Phase 6  Governance + Admin         Weeks 16–17
Phase 7  Hardening + V1 Release     Weeks 18–19
─────────────────────────────────────────────────
V1 GA                               ~19 weeks (4.5 months)
Phase 8  V2 (multi-agent, SSO)      Post-GA, ~8–10 weeks
Phase 9  V3 (integrations, SCIM)    Demand-driven
```

Dependency chain: 0 → 1 → 2 → 3 → 4 → 5; 6 runs partially in parallel with 5 (instrumentation already exists from Phase 3); 7 gates GA.

---

## Phase 0 — Foundation (Weeks 1–2)

**Goal:** A deployable skeleton that fails fast, isolates tenants, and cannot regress.

**Scope:** Monorepo, typed config, async DB core + RLS tenant context, error envelope + structured logging, Identity domain (orgs, users, JWT auth with rotating refresh tokens + reuse revocation, RBAC), health probes, full CI pipeline, ADRs 0001–0004.

**Out of scope:** Everything AI. No LLM dependency exists yet by design.

**Exit criteria:**
- SPRINT-1.md exit criteria + robustness checklist fully ticked
- register → login → refresh → protected route proven by tests
- Tenant isolation suite green under forced RLS with a non-bypassrls runtime role
- All 8 CI jobs blocking and green

---

## Phase 1 — Knowledge Domain: Ingestion & Lifecycle (Weeks 3–5)

**Goal:** Documents go in, become chunks + embeddings, and can be versioned and deleted — reliably, asynchronously, and idempotently.

**Scope:**
- Knowledge collections CRUD with per-collection ACLs (groundwork for Phase 2 retrieval filtering)
- Upload endpoint → object storage (S3-compatible) → worker queue
- Ingestion state machine (queued → processing → ready/failed) per patterns.md §3, compare-and-set claims, bounded retries, stuck-task sweeper
- Parsers: PDF (incl. OCR for scanned), DOCX, PPTX, XLSX, TXT; format-aware chunking strategies (prose / table / slide) as pure functions
- Embedding generation behind an `Embedder` Protocol (provider adapter + fake); upsert keyed by content hash
- Document lifecycle: re-upload versioning, cascading deletion (chunks, embeddings, index entries), re-index command
- Quotas and limits enforced (file size, page count, per-org document quota) via rules.py

**Out of scope:** Search/retrieval (Phase 2), any LLM completion calls.

**Key risks:** Malformed real-world files. Mitigation: parser fallback chain, partial-failure surfacing, a corpus of deliberately broken test fixtures in the repo.

**Exit criteria:**
- 100-page text PDF: upload → READY < 5 min locally; status visible at every stage
- Kill a worker mid-task → sweeper re-queues; re-run produces identical end state (idempotency test)
- Delete document → zero orphaned chunks/embeddings/files (verified by test)
- Broken-fixture corpus: every file ends in FAILED with a human-readable reason, never stuck
- Quota breach returns typed 4xx envelope; isolation suite extended to knowledge tables, green

---

## Phase 2 — Search Domain: Permission-Aware Retrieval (Weeks 6–7)

**Goal:** Hybrid search that is fast, relevant, and structurally incapable of leaking documents across users or tenants.

**Scope:**
- pgvector HNSW index (parameters configured in one place per CLAUDE.md §6)
- Vector search + keyword search (Postgres FTS) + reciprocal rank fusion
- Permission-aware retriever: org_id + collection-ACL filtering inside the query, before similarity ranking — the only retrieval entry point in the codebase
- Metadata filtering (collection, format, date range), cursor pagination, source attribution payloads
- Retrieval golden set v0 (≥50 query→expected-chunk pairs from seeded fixtures); `make eval-retrieval` reporting recall@5 / MRR

**Exit criteria:**
- p95 < 3s on a seeded corpus of 100k chunks (measured, recorded in docs)
- ACL test: user without collection access gets zero chunks from it — by search, by direct chunk ID, and by metadata filter abuse
- recall@5 ≥ 0.85 on golden set v0; eval wired into CI as the Phase-2 regression gate
- Isolation suite extended to chunks/embeddings, green

---

## Phase 3 — AI Core: Gateway, Prompts, Guardrails (Weeks 8–10)

**Goal:** One disciplined doorway to every LLM — routed, traced, metered, guarded — before any feature consumes it.

**Scope:**
- `LLMGateway` Protocol + LiteLLM adapter: per-org default model, ordered fallback chain, timeouts, bounded retry with backoff+jitter, circuit breaker
- Versioned prompt template registry (template version logged on every response)
- Guardrail chain as composed steps: prompt-injection filtering on retrieved content, configurable PII redaction, output schema validation with bounded re-ask
- Structured output support (JSON schema validated)
- Langfuse tracing wired in the adapter: prompt version, model, tokens, latency, source IDs
- Token metering per org/user + monthly budgets (soft warn / hard cap) enforced in the gateway
- `FakeGateway` for all tests; generation eval harness skeleton (LLM-as-judge rubric, versioned)

**Out of scope:** Any user-facing AI feature. This phase ships infrastructure consumed by Phases 4–5.

**Exit criteria:**
- Provider outage simulation: fallback chain engages; all providers down → typed 503, never a hang or fabricated output
- Budget hard cap blocks the next call with typed 4xx; metering numbers reconcile with traced token counts
- Injection fixture corpus: known attack strings in retrieved content are neutralized (test suite)
- Zero provider SDK imports outside `adapters/` (import-linter contract added)
- Every gateway call visible as a complete trace in Langfuse locally

---

## Phase 4 — Conversation Domain: Grounded Chat (Weeks 11–12)

**Goal:** Multi-turn chat over the knowledge base that cites or refuses — never fabricates.

**Scope:**
- Chat sessions (create/resume/archive/delete), persisted history, per-message citations stored with the message
- Context assembly: last-N turns + rolling summary for long sessions (pure, unit-tested)
- Follow-up handling: query rewriting using conversation context before retrieval
- GroundedAnswerPipeline (patterns.md §5): retrieve → assemble → confidence check → generate → grounding validation; refusal as a typed first-class result
- SSE streaming responses; per-message feedback (👍/👎 + comment) persisted
- Frontend: chat UI with loading/empty/error/refusal states, citation rendering, session list

**Exit criteria:**
- Answerable golden queries → answers with ≥1 valid citation each; unanswerable queries → explicit refusal (no hallucinated answer) — both proven by eval run
- Faithfulness ≥ 0.9 on generation golden set v0
- First token < 3s, full response p95 < 15s on local reference corpus
- Conversation history isolated per user (extends isolation suite); citations resolve to chunks the user is permitted to see

---

## Phase 5 — Recommendations + Reports (Weeks 13–15)

**Goal:** Decision outputs — structured recommendations and exportable reports — through the V1 fixed agent pipeline.

**Scope:**
- Recommendation flow: permission-aware retrieval → context analysis → grounding validation → structured output (recommendation, alternatives, justifications with citations, confidence band derived from retrieval scores — never model self-report); below-threshold → typed decline with reason
- Fixed LangGraph pipeline (Knowledge Agent → Report Agent) per PRD Domain 6 V1 scope — linear graph, no dynamic planning
- Report templates: executive summary, technical report, recommendation report; generation metadata embedded (model, prompt version, timestamp)
- PDF export pipeline (HTML → PDF), async via worker queue for long reports, download endpoint
- Frontend: recommendation view, report builder, export/download states

**Exit criteria:**
- Recommendation golden set: structured outputs validate against schema 100%; confidence bands monotonic with retrieval scores (property test)
- Below-threshold queries decline with reason — zero forced recommendations (eval-proven)
- PDF export: 30-page report generates < 10 min, renders citations and metadata correctly
- LangGraph pipeline steps individually unit-tested with FakeGateway; full pipeline integration test green

---

## Phase 6 — Governance + Administration (Weeks 16–17)

**Goal:** Surface what Phases 0–5 already record — turn instrumentation into operator and admin capability.

**Scope:**
- Audit log query API + admin UI (immutable, append-only store finalized; filter by actor/action/resource/date)
- Usage analytics: tokens, searches, documents, active users per org (aggregates from metering data)
- Admin surfaces: org/user administration, quota + budget management, AI configuration (default model, fallback chain, guardrail toggles), document/knowledge-base administration
- Retention enforcement jobs: audit-log retention, org-configurable policies
- Feedback review queue → golden-set curation workflow (closes the eval loop)

**Parallelism note:** Can start during Phase 5 week 2 — it consumes data models that already exist; no new AI surface.

**Exit criteria:**
- Every mutating action from Phases 0–5 appears in the audit log (coverage test enumerates service methods)
- Admin RBAC: member cannot reach any admin endpoint (403 envelope, tested)
- Budget changed in admin UI → enforced on the next gateway call (integration test)
- Retention job: expired audit rows purged on schedule; deletion itself audited

---

## Phase 7 — Hardening + V1 Release (Weeks 18–19)

**Goal:** Prove the system holds under load, attack, and failure — then ship.

**Scope:**
- Load test at reference load (100 concurrent users, 1M chunks seeded): verify NFR targets, fix regressions
- Failure drills (scripted, repeatable): DB restart mid-traffic, provider outage during chat, worker death mid-ingestion, disk-full on object storage — each must degrade per design, recover without manual surgery
- Security pass: dependency audit clean, OWASP top-10 review against the API, secrets rotation drill (runbook executed for real), rate-limit tuning
- Golden sets expanded (≥150 retrieval, ≥75 generation pairs); full eval baseline recorded as the V1 quality contract
- Backup/restore drill: restore from backup into a clean environment, runbook timed against RTO 4h
- Docs freeze: ADRs current, runbooks tested, deployment guide, versioned API docs
- Tag `v1.0.0`, deploy, smoke suite against production

**Exit criteria (V1 GA gate):**
- All PRD §12 platform/quality metrics measured and meeting targets (recall@5 ≥ 0.85, faithfulness ≥ 0.9, search p95 < 3s, ingestion success ≥ 98%)
- Every failure drill passes with zero data loss and zero manual database edits
- Restore drill completes within RTO; isolation suite green one final time on the release commit
- Zero open severity-1/2 defects

---

## Phase 8 — V2 (Post-GA, ~8–10 weeks)

Ordered by value: SSO (OIDC/SAML) → dynamic multi-agent workflows (Research, Recommendation, Compliance agents; conditional LangGraph routing) → advanced multi-option recommendations with policy validation → analytics dashboard → multilingual support. Each item gets its own sprint spec written against CLAUDE.md + patterns.md before any code.

## Phase 9 — V3 (Demand-driven)

Enterprise connectors (SharePoint/Drive/CRM), SCIM provisioning, workflow automation, industry modules. Prioritized strictly by signed customer demand — no speculative builds.

---

## Standing Rules Across All Phases

1. **Phase gates are binary.** A phase is open or closed; "90% done" is open.
2. **One sprint spec per phase**, written in the SPRINT-1.md format (ordered tasks, VERIFY blocks) before the phase starts.
3. **Golden sets only grow.** Eval baselines from each phase become permanent regression gates for all later phases.
4. **Scope changes go through the PRD.** Mid-phase feature ideas are recorded, not built; the roadmap is re-cut only at phase boundaries.
5. **Two-week slip rule.** If a phase exceeds its estimate by 2+ weeks, stop and re-plan the remaining phases rather than compressing quality gates.
