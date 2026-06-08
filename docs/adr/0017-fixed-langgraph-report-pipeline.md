# ADR 0017 — Fixed, Linear LangGraph for Report Generation (no dynamic routing in V1)

## Context

Phase 5 introduces multi-section reports generated from retrieved evidence. The work is
naturally two steps — (1) retrieve + assemble grounded context, (2) generate the structured,
cited report — and we adopt LangGraph (ADR pinning `langgraph==0.2.76`) to express it as a
typed state graph rather than ad-hoc orchestration.

LangGraph's headline feature is *dynamic* routing: a node (often the model itself) decides the
next node, enabling loops, tool-choosing agents, and conditional fan-out. That power is also
its risk: a model-decided next step is non-deterministic, hard to test exhaustively, hard to
budget (unbounded loops), and hard to reason about for security (every reachable node is an
attack surface). The recommendation/report invariants — decline-or-cite, retrieval-derived
confidence, "infra failure ≠ content decline", one structured call per generation — depend on a
*predictable* control flow.

## Decision

**The report graph is FIXED and LINEAR for V1: `knowledge_agent → report_agent → END`, every
time, with no model-decided routing.** The only conditionality is a *typed terminal* carried in
the state, not a branch in the graph:

- `knowledge_agent` (node 1) retrieves permission-aware candidates and assembles the grounded
  context. With fewer than `report_min_sources` permitted chunks it sets
  `error = INSUFFICIENT_EVIDENCE` (a content decline) and node 2 passes through — no generation
  spend. It makes **no** LLM call.
- `report_agent` (node 2) makes exactly **one** structured generation call
  (`response_schema = ReportContent`, JSON mode + bounded re-ask) and ground-validates every
  section's citations. If no section survives grounding it sets `error = NO_GROUNDED_SECTIONS`.

Both nodes return partial state updates. Node errors are **typed terminal STATE** (`error` in
the `ReportState` TypedDict), never exceptions escaping the graph. A genuine infrastructure
failure (schema-invalid after re-ask, gateway outage, budget block) propagates as a typed
`AppError` and is handled by the worker as a retryable failure — it is **never** conflated with
a content decline (the Phase-4 split, reused). Dependencies (retriever, gateway, registry,
actor, settings) are bound once per request via `functools.partial`; the graph is compiled and
run with `await graph.ainvoke(initial_state)`.

Dynamic multi-agent orchestration (a planner choosing sections, tool-using sub-agents, revision
loops) is explicitly **V2**.

## Consequences

- **Deterministic + exhaustively testable.** Two nodes, three terminals (content report,
  `INSUFFICIENT_EVIDENCE`, `NO_GROUNDED_SECTIONS`) — each unit-tested with a `FakeGateway`, plus
  a graph-integration test per terminal. No path explosion.
- **Budget-safe.** Exactly one generation call per report; no loop can run up token spend. The
  decline gate in node 1 spends zero before retrieval clears the floor.
- **Security-bounded.** Every reachable node is known statically; the retrieval ACL (Phase 2)
  is the only data path and is re-proven through the report surface in the isolation suite.
- **Honest failure.** A report that cannot be grounded ends FAILED with a reason
  (`NO_GROUNDED_SECTIONS`), never an empty-but-successful report — gated by the report eval
  (Task 9), which exercises that terminal explicitly.
- **Cost of the constraint.** No cross-section planning or self-revision in V1; section quality
  rests on a single structured call against the per-type template. Acceptable for V1; the
  revisit trigger is a concrete product need for adaptive, multi-pass reports — at which point
  the linear graph gains conditional edges, and this ADR is superseded rather than edited.

Supersedes nothing. Related: ADR 0013 (eval determinism caveat), ADR 0016 (retrieval-derived
confidence), ADR 0018 (PDF export library).
