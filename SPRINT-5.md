# OnMixAI — Sprint 5 Specification (Phase 4: Conversation / Grounded Chat)

Goal: multi-turn chat over the knowledge base that cites or refuses — never fabricates — streamed over SSE, with persisted sessions, per-message citations and feedback, and the platform's first real frontend (login, session list, chat). Everything flows through Phase 2's PermissionAwareRetriever and Phase 3's gateway; this sprint adds no new provider or retrieval surface.

Execution rules: as prior sprints. Review pauses: after Task 5 (grounded pipeline), after Task 8 (frontend), and the final report.

Sprint 5 exit criteria (roadmap Phase 4):
1. Answerable golden queries → answers with ≥1 valid citation each; unanswerable queries → explicit typed refusal, zero fabricated answers — both eval-proven.
2. Faithfulness ≥ 0.9 on generation golden set (chat path).
3. First token < 3s; full response p95 < 15s on the local reference corpus (measured, method recorded).
4. Conversation history isolated per user (owner-scoped, stricter than org); citations resolve only to chunks the requesting user is permitted to see.
5. All prior gates green; coverage ≥ 80%.

Core design decision (ADR 0014, written in Task 5): **streaming with terminal validation.** Tokens stream as generated; grounding validation runs on the completed text. The SSE protocol therefore has terminal events that can supersede streamed content: a `refusal` terminal event instructs the client to replace the streamed text with the refusal state. Validation cannot be done on a prefix; pretending otherwise produces either fake streaming (buffer-then-flush) or unvalidated output. This is the honest semantics — spec'd, tested, and surfaced in the UI as a designed state.

---

## Task 1 — Conversation schema + migration 0007

`conversation/models.py` (org_id NOT NULL + forced RLS in-migration on all, as always):
- `chat_sessions`: id, org_id, owner_user_id (NOT NULL, idx), title (nullable — derived after first exchange), is_archived (bool default false), created_at, updated_at, last_message_at. Index (org_id, owner_user_id, last_message_at DESC).
- `chat_messages`: id, org_id, session_id (fk ON DELETE CASCADE), role (enum: user|assistant), content (text), citations (jsonb — list of {chunk_id, document_id, marker_index, page_ref}; empty for user messages), refusal_reason (nullable — set when the assistant message is a refusal), prompt_version (nullable), model_used (nullable), trace_id (nullable), seq (int, per-session monotonic), created_at. Unique (session_id, seq). Index (org_id, session_id, seq).
- `message_feedback`: id, org_id, message_id (fk ON DELETE CASCADE), user_id, rating (enum: up|down), comment (nullable, ≤2000 chars), created_at; unique (message_id, user_id).
- `session_summaries`: id, org_id, session_id (fk ON DELETE CASCADE, unique), summary (text), through_seq (int — last message included), prompt_version, updated_at. (Rolling summary storage for Task 3.)

**VERIFY**
```
cd backend
alembic upgrade head && alembic downgrade -1 && alembic upgrade head && alembic check
# RLS t|t on all four; runtime-role CRUD under GUC
pytest tests/conversation/test_models.py -q
```
Commit: `feat(conversation): session/message/feedback/summary schema with forced RLS`

---

## Task 2 — Domain skeleton: ownership rules, schemas

`conversation/rules.py` (pure, branch-complete):
- `ensure_session_owner(session, user_id)` — the per-user isolation rule: sessions are private to their owner even within an org (NotFound, not Forbidden — no existence oracle, consistent with Phase 2).
- `ensure_session_active(session)` (archived → ConflictError), `validate_message_content` (1–8000 chars, normalized), `derive_title(first_user_message)` (truncated, whitespace-collapsed), `next_seq(current)`.
- Limits in Settings: `chat_max_sessions_per_user: int = 200`, `chat_history_turns: int = 10`, `chat_summary_threshold_turns: int = 16`, `chat_context_token_budget: int = 6000`.

`conversation/schemas.py`: SessionResponse, MessageResponse (content, citations with resolved SourceAttribution, refusal_reason, feedback state), SSE event payload models (Task 6 wire format — defined here so frontend codegen sees them). `exceptions.py`: SESSION_NOT_FOUND, SESSION_ARCHIVED, MESSAGE_TOO_LONG, SESSION_LIMIT_EXCEEDED.

**VERIFY**
```
cd backend && pytest tests/conversation/test_rules.py -q   # branch-complete incl. ownership NotFound semantics
mypy src/ && ruff check .
```
Commit: `feat(conversation): ownership rules, limits, schemas`

---

## Task 3 — Context assembly (pure) + rolling summary

`conversation/context.py` — pure assembly per patterns.md (zero I/O; service gathers, assembler decides):
- `assemble_context(history: list[Message], summary: Summary | None, retrieved: list[ScoredChunk], budget_tokens) -> AssembledContext` — packing order: system frame → summary (if present) → last-N turns (N = chat_history_turns, newest-complete-first trimming when over budget) → retrieved chunks (already guardrail-framed). Deterministic trimming with documented priority: retrieved chunks are never trimmed below the confidence-check minimum; history trims first, oldest first. Complexity annotated (single pass, O(t + c)).
- Rolling summary: when a session exceeds `chat_summary_threshold_turns`, the worker (ARQ — reuse Phase 1 infra) generates/updates `session_summaries` via `summarize_session@1.0.0` template through the gateway, covering messages ≤ through_seq. Summary generation is async and best-effort: assembly works with a stale or absent summary (falls back to more raw turns within budget). Summary job is idempotent (through_seq compare-and-set).

**VERIFY**
```
cd backend && pytest tests/conversation/test_context.py tests/conversation/test_summary_job.py -q
# property tests: assembled context never exceeds budget; chunk floor respected; trimming
# deterministic; absent/stale summary degrades gracefully; summary job CAS-idempotent
mypy src/ && ruff check .
```
Commit: `feat(conversation): pure context assembly with async rolling summaries`

---

## Task 4 — Follow-up query rewriting

`conversation/rewrite.py`: `rewrite_query(history_tail, raw_query) -> RewrittenQuery` via `rewrite_query@1.0.0` template (gateway, feature=chat, tiny token cost) — resolves anaphora ("what about the second option?") into a standalone retrieval query. Hard rules: rewriting is best-effort and NEVER blocks — on gateway failure/timeout/budget-block, fall back to the raw query (typed `RewriteFallback` recorded in trace, not an error); rewritten query goes to retrieval only, the user's original text is what's stored and shown; first message in a session skips rewriting entirely (nothing to resolve).

**VERIFY**
```
cd backend && pytest tests/conversation/test_rewrite.py -q
# fallback on every failure class (scripted fake); skip-on-first-message; rewrite output
# capped/sanitized before hitting retrieval; trace records used-vs-fallback
mypy src/ && ruff check .
```
Commit: `feat(conversation): best-effort follow-up query rewriting with hard fallback`

---

## Task 5 — GroundedAnswerPipeline  [PAUSE after VERIFY]

`conversation/pipeline.py` — the composed flow (patterns.md §5), consuming only existing ports:
1. `rewrite` (Task 4) → 2. `retrieve` (PermissionAwareRetriever — THE only retrieval entry) → 3. `confidence check` (pure: top-score + result-count thresholds from Settings; below → `Refusal("INSUFFICIENT_SOURCES")` BEFORE any generation spend) → 4. `assemble` (Task 3) → 5. `generate` via gateway with `grounded_answer@1.1.0` — the template instructs inline citation markers `[1]..[n]` mapped to a provided source list → 6. `grounding validation` (pure): every marker in the text must map to a provided source; an answer with ZERO markers → `Refusal("UNGROUNDED_ANSWER")`; markers referencing nonexistent sources → strip + if none survive, refusal. Citations persisted are the validated set only.

Refusals are first-class `Refusal(reason)` results (Phase 3 types) — persisted as assistant messages with refusal_reason set, traced, and eval-countable. ADR 0014: streaming-with-terminal-validation semantics (decision, alternatives — buffer-then-flush and prefix-validation — and why they lose).

**VERIFY**
```
cd backend && pytest tests/conversation/test_pipeline.py -q
# scripted-fake matrix: happy path (markers validate, citations persisted = validated set);
# zero-marker answer → UNGROUNDED refusal; phantom-marker stripped, none-survive → refusal;
# low confidence → refusal BEFORE generate (call-log: zero gateway completion calls);
# retrieval empty → refusal; every step's failure class → typed outcome, never fabrication;
# citations in persisted message reference only retrieved (= permitted) chunk ids
mypy src/ && ruff check .
```
Commit: `feat(conversation): grounded answer pipeline — cite or refuse, validated citations only`

---

## Task 6 — Chat API: sessions + SSE streaming + feedback

Session endpoints (thin routers, 6-step services, all owner-scoped): `POST /api/v1/chat/sessions` (limit-checked), `GET /sessions` (cursor-paginated, owner's only, last_message_at desc), `PATCH /sessions/{id}` (archive/rename), `DELETE /sessions/{id}` (cascade), `GET /sessions/{id}/messages` (paginated, citations hydrated with SourceAttribution).

`POST /api/v1/chat/sessions/{id}/messages` — SSE stream (`text/event-stream`). Event protocol (versioned, schema'd in Task 2):
- `meta` {message_id, seq} — immediately on accept (user message persisted, seq allocated)
- `token` {text} — streamed deltas from the gateway's streaming path
- `citations` {items} — terminal, validated set
- `done` {message_id, prompt_version, trace_id}
- `refusal` {reason} — terminal, supersedes streamed tokens (client replaces content — ADR 0014)
- `error` {code} — typed envelope codes only, never internals

Mechanics: heartbeat comment every 15s (proxy keep-alive); client disconnect → generation cancelled (CancelledError handled — partial assistant message NOT persisted; user message + an assistant row with refusal_reason="CLIENT_DISCONNECTED" are not written either — the turn simply has no assistant message and is re-askable); rate limit 30 messages/min per user (established adapter); audit `chat.message_sent` (length + session_id, never content — content lives in the message table, governed by retention). Gateway streaming support: extend `LLMGateway` with `complete_stream` (Protocol + fake + adapter + stub support) — fake scriptable per-token for deterministic tests.

**VERIFY**
```
cd backend && pytest tests/conversation/test_api.py tests/conversation/test_sse.py -q
# event-order proof: meta→token*→citations→done (happy); meta→token*→refusal (post-validation);
# meta→refusal (pre-generation, low confidence — zero token events);
# disconnect mid-stream → no assistant row persisted, gateway call cancelled (fake records cancel);
# non-owner session access → 404 (no oracle); rate limit 429; feedback upsert (one per user/message)
mypy src/ && ruff check .
```
Commit: `feat(conversation): chat API with SSE streaming, terminal validation events, feedback`

---

## Task 7 — Frontend foundation: generated client, auth, shell

First real frontend work — CLAUDE.md §10 frontend rules now fully active:
- OpenAPI client generation from the FastAPI schema (`npm run generate:api` — openapi-typescript + a thin typed fetch/SSE wrapper; generated code committed, regeneration check in CI: schema change without regenerated client fails).
- Auth: login page (org slug + email + password), token storage in memory + silent refresh via the rotating refresh flow (httpOnly-cookie refresh if same-origin; otherwise documented memory-only trade-off — decide and record in ADR 0015), authenticated route guard, logout.
- App shell: layout, nav (Chat / Documents placeholder), error boundary, toast system for typed error envelopes (code → message map), TanStack Query setup (no server data in global stores).

**VERIFY**
```
cd frontend && npm run generate:api && git diff --exit-code src/lib/api/ \
  && npx tsc --noEmit && npm run lint && npm test && npm run build
# auth flow tests (msw): login → guarded route → silent refresh on 401 → logout;
# every typed backend error code renders a human message (exhaustiveness test over the code enum)
```
Commit: `feat(frontend): generated API client, auth flow, app shell`

---

## Task 8 — Chat UI  [PAUSE after VERIFY]

`frontend/src/features/chat/`: session list (paginated, archive/rename/delete), chat view:
- SSE consumption of the Task 6 protocol; streaming token rendering; **refusal-supersede behavior implemented exactly per ADR 0014** — streamed text is replaced by a designed refusal state (distinct visual treatment + reason copy), not left dangling.
- Citation rendering: inline `[n]` markers → hoverable/tappable source cards (filename, page/slide ref) from the `citations` event; sources panel per message.
- All five async states designed, not defaulted: loading (skeleton), streaming (live region, stop button — client-side cancel), empty session, error (typed code → message + retry), refusal (explanatory, suggests rephrasing/uploading docs). Feedback UI (up/down + optional comment) per assistant message.
- Accessibility per CLAUDE.md: streaming announcements via aria-live polite, keyboard-complete (compose, send, navigate sessions, open citations), focus management on session switch.

**VERIFY**
```
cd frontend && npx tsc --noEmit && npm run lint && npm test && npm run build
# component tests (msw + scripted SSE): token accumulation; refusal supersedes streamed text;
# citations render + resolve; stop button cancels (no further tokens applied);
# all five states snapshot-covered; axe (jest-axe) passes on chat view
# manual smoke vs live stack: login → create session → ask (stub) → streamed grounded answer
#   with citations → ask unanswerable → refusal state → feedback → archive
```
Commit: `feat(frontend): streaming chat UI with citations, refusal states, feedback`

---

## Task 9 — Chat eval + latency drill

- `tests/golden/chat_v0.jsonl` (≥40 cases over the seeded corpus): answerable (expected: ≥1 citation, faithful), unanswerable (expected: refusal — measures refusal correctness both ways), follow-up pairs (rewrite quality: retrieval after rewrite must hit the planted chunk), citation-precision cases (markers map to genuinely supporting chunks — judged).
- `make eval-chat`: runs the full real path (rewrite → retrieve → pipeline) against stub; reports answerable-recall, refusal-correctness (no wrong-refusals on answerable / no answers on unanswerable), faithfulness (judge), citation validity rate. Thresholds: faithfulness ≥ 0.9, refusal-correctness ≥ 0.95, zero fabricated citations. CI job `eval-chat`, path-triggered on conversation/ + ai/ + search/.
- Latency drill `scripts/drills/chat_latency.sh`: 100 messages over the reference corpus through the live stack (stub with realistic injected delay — document the delay model), measure first-token and full-response p50/p95 from the SSE client side; record method + numbers in `docs/benchmarks/chat_latency.md` with the stub caveat (real-model numbers re-measured when a real provider is configured — revisit trigger).

**VERIFY**
```
cd backend && make eval-chat        # thresholds met, deterministic across two runs
bash scripts/drills/chat_latency.sh # first-token < 3s, full p95 < 15s, numbers recorded
```
Commit: `feat(conversation): chat golden set, eval gate, latency drill`

---

## Task 10 — Isolation extension, docs, phase exit

- Isolation suite extension — the new axis is USER-level within an org: user A1 cannot list/read/message/delete user A2's sessions (same org) — by list, by direct id (404), by message access, by feedback on others' messages; plus the standard cross-org axis and raw-count RLS proofs on all four tables; citation-hydration path cannot resolve chunks outside the requester's ACL (the Phase 2 guarantee re-proven through the chat surface).
- Docs: `conversation/README.md` (cite-or-refuse invariant, SSE protocol contract, ownership semantics, summary lifecycle), ADR 0014 (streaming + terminal validation), ADR 0015 (frontend token-storage decision), runbook `chat-quality-regression.md` (reading eval deltas, feedback-queue triage).
- Final report: five exit criteria one by one, robustness checklist, full local gates (now incl. eval-chat) on closing commit, standing items.

**VERIFY**
```
cd backend && make verify && pytest tests/isolation/ -q     # full isolation: identity+knowledge+search+conversation
cd frontend && npx tsc --noEmit && npm run lint && npm test && npm run build
alembic downgrade base && alembic upgrade head              # 0001→0007 clean
git grep -nE "TODO|FIXME" -- src/ ../frontend/src ; test $? -ne 0
```
Commit: `docs,test: phase 4 exit — user-level isolation, ADRs, runbooks, final gates`

---

## Phase 4 robustness checklist (final gate)

- [ ] Every assistant message is either cited (validated markers only) or a typed refusal — no third state exists in the schema or the eval results
- [ ] Low-confidence refusal happens BEFORE generation (zero completion calls proven)
- [ ] Phantom citations stripped; zero fabricated citations in eval; persisted citations ⊆ retrieved (= permitted) chunks
- [ ] Refusal-supersede works end-to-end: backend terminal event + UI replacement state (tested both sides)
- [ ] Client disconnect mid-stream: generation cancelled, no partial assistant row, turn re-askable
- [ ] Query rewriting can never block or fail a message (fallback proven per failure class)
- [ ] Context assembly never exceeds budget; chunk floor respected; summary staleness degrades gracefully
- [ ] Sessions strictly owner-private within org (404 semantics); full isolation suite green across all four domains
- [ ] First-token < 3s and full p95 < 15s recorded with method + stub caveat; eval-chat gate blocking in CI
- [ ] Frontend: all five states designed incl. refusal; axe-clean; generated client in lockstep with schema (CI-enforced)
