# conversation — grounded chat

Multi-turn, permission-aware chat over a tenant's documents. Every assistant turn is either
an answer grounded in retrieved sources with validated citations, or a typed refusal — never
a fabricated or ungrounded answer.

## Responsibility

- Chat sessions (private per user within a tenant) and their message history.
- The grounded-answer pipeline: rewrite → retrieve → confidence → assemble → generate →
  grounding-validate (patterns.md §5), streamed to the client (ADR 0014).
- Per-message feedback (thumbs up/down) and a rolling per-session summary.

It owns no retrieval or LLM surface of its own: retrieval goes through `search.SearchService`
(the only retrieval entry, ACL-filtered in the SQL predicate), and generation through the
shared metered+traced `LLMGateway`. SDK imports are confined to `ai/adapters/` (import-linter).

## Public service interface (`ChatService`)

- `create_session / list_sessions / update_session / delete_session` — session CRUD, keyset
  pagination, per-user cap.
- `list_messages` — one page of a session's messages with the requester's own feedback.
- `send_message_stream(actor, session_id, content, *, request_id)` — async generator of the
  SSE protocol events; persists the turn atomically at the terminal (see below).
- `submit_feedback` — thumbs up/down on an assistant message the requester can see.

## Invariants

- **Cite-or-refuse, in storage not just in flight.** An assistant row carries either validated
  `citations` (every marker maps to a provided source; phantoms stripped) OR a `refusal_reason`
  — never both, never neither. Enforced by the pipeline + grounding rules; re-proven by the
  scripted-fake matrix (`tests/conversation/test_pipeline.py`) and the SSE matrix
  (`test_sse.py`).
- **Refusal vs. error (ADR 0014).** A *content* outcome (insufficient sources, ungrounded) is a
  typed `Refusal` — persisted and counted by the chat eval. An *infrastructure* failure
  (provider outage, budget block — a gateway `AppError`) is NOT a refusal: it propagates, the
  SSE layer emits a terminal `error` event, no assistant row is persisted, and the turn is
  re-askable. Refusal-correctness counts only content refusals.
- **Phantom-citation floor.** If the phantom fraction of an answer's markers reaches
  `chat_max_phantom_fraction` (default 0.5 = parity with real markers), the whole answer is
  refused as `UNGROUNDED_ANSWER`; below it phantoms are stripped. `phantom_marker_count` is
  recorded in the grounding trace (invention rate, eval).
- **Low-confidence refuses before generation** — zero spend, no tokens streamed.
- **Atomic persistence at the terminal.** The user + assistant messages are written together
  only once a content terminal (answer/refusal) is reached; a client disconnect or an infra
  failure mid-stream persists nothing.
- **Two isolation axes.** Sessions are private to `owner_user_id` *within* a tenant, on top of
  org-level RLS. A session/message owned by another user (same org) or another org is
  indistinguishable from missing (404, no oracle). Proven by `tests/isolation/
  test_conversation_isolation.py` (per-user + cross-org + raw-count RLS on all four tables +
  the retrieval-ACL re-proof through the chat surface).

## SSE protocol (ADR 0014)

```
meta → token* → citations → done     grounded answer (citations are the validated set)
meta → token* → refusal               validation failed AFTER streaming — supersede
meta → refusal                         low confidence — refused BEFORE generation
meta → [token*] → error                infrastructure failure — no assistant row, re-askable
```

Grounding can only be validated on the complete answer, so tokens stream live and a terminal
event supersedes them. The client implements the refusal-supersede as a designed state (it
replaces the streamed text); a heartbeat keeps idle connections alive; a client disconnect
cancels generation. Caller errors (archived session, bad input, not owner) surface as a normal
JSON 4xx (the stream is primed before the response is constructed), not as a stream event.

## Ownership semantics

Every method takes the actor's `AuthContext`. Repositories are tenant-scoped (`org_id` +
RLS); the per-user ownership rule (`ensure_session_owner`) is applied in `rules.py` over what
they return. A repository method touching conversation data without tenant context does not
exist.

## Summary lifecycle

A session past `chat_summary_threshold_turns` gets a rolling summary maintained by an
idempotent ARQ worker task: it summarizes through a `through_seq` and upserts with a
compare-and-set on `through_seq`, so a slow/out-of-order job can never overwrite a fresher
summary. The summary is context-only input to assembly — never treated as a citable source.

## Known limitations

- Streaming generation uses a single circuit-allowed model with no mid-stream retry/fallback
  (the non-streaming path keeps the full resilience chain); an error after the first token
  surfaces as a terminal `error` event.
- The golden-set eval (`make eval-chat`) is deterministic harness-correctness against the
  stub, not real-model quality; faithfulness/latency are re-measured against a real provider
  when one is configured (see `docs/benchmarks/chat_latency.md`).
