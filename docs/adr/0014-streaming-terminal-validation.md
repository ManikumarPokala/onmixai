# ADR 0014 — Streaming Chat with Terminal Validation

Status: Accepted (2026-06-07)

## Context

Chat must (a) stream tokens as they are generated (good UX, first-token latency) and
(b) guarantee the cite-or-refuse invariant: an assistant message is either grounded with
validated citations or a typed refusal — never fabricated, never ungrounded. These pull
in opposite directions, because grounding can only be validated on the **complete**
answer: citation markers `[n]` must be checked against the provided sources, and "zero
markers → ungrounded" cannot be decided from a prefix. You cannot validate what hasn't
finished generating.

## Decision

**Stream tokens, validate on completion, and let a terminal event supersede.** The SSE
protocol (Task 6) is:

```
meta → token* → citations → done          (grounded answer)
meta → token* → refusal                    (validation failed AFTER streaming)
meta → refusal                             (low confidence — refused BEFORE generation)
```

- Tokens stream live (`token` events) as the gateway produces them.
- When generation completes, grounding validation runs on the full text. If it passes,
  a terminal `citations` event carries the validated set, then `done`.
- If it fails (zero/all-phantom markers, or a generation error), a terminal `refusal`
  event is emitted. **The client replaces the streamed text with the refusal state** —
  the refusal supersedes whatever was shown. This is implemented in the UI (Task 8) as a
  designed state, not left dangling.
- The low-confidence refusal happens *before* generation, so that path streams no tokens
  at all (`meta → refusal`) and spends nothing.

## Alternatives rejected

- **Buffer-then-flush** (generate fully, validate, then "stream" the buffered text):
  this is *fake* streaming — first-token latency equals full-response latency. It throws
  away the entire UX benefit to preserve a tidy "only validated text is ever shown"
  story. Rejected: the honest cost (a possible terminal supersede) is far cheaper than
  fake streaming for every answer.
- **Prefix / incremental validation** (validate as tokens arrive): grounding is not a
  prefix property. A marker `[2]` mid-stream may be contradicted by the rest of the
  answer; "the answer cited nothing" is only knowable at the end. Partial validation
  would either pass ungrounded prefixes or block valid ones. Rejected: it pretends a
  whole-text property is incremental.

## Consequences

- The refusal-supersede is a **first-class, designed state on both sides**: the backend
  emits a terminal `refusal` event (tested for event order), and the UI replaces the
  streamed content with an explanatory refusal (tested for the replacement). It is not
  an error or an edge case — it is the honest semantics of "stream, then validate."
- A user may briefly see streamed text that is then replaced by a refusal. This is
  acceptable and surfaced clearly (distinct treatment + reason), and is rare in practice
  (the model is instructed to cite or say it lacks information).
- Client disconnect mid-stream cancels generation; no partial assistant message is
  persisted, and the turn is re-askable (Task 6).
- Persisted citations are always the validated set only — the schema has no "answer
  without validated citations" state (the cite-or-refuse invariant holds in storage,
  not just in flight).
