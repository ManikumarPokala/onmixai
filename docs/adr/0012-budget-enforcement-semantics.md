# ADR 0012 — Token Budget Semantics: Pre-Block on the Materialized Total, Exact Post-Record

Status: Accepted (2026-06-07)

## Context

Per-org token budgets are enforced in the gateway (CLAUDE.md §6). Two facts force a
design choice: (1) we don't know a call's token cost until it returns, and (2) a budget
check is on the hot path, so it must be O(1). The question is what exactly the hard cap
guarantees, and how the running total is maintained correctly under concurrency.

## Decision

**Pre-block on the materialized period total; record the exact cost after.**

- **Pre-call (O(1)).** The gateway reads `token_usage_periods.total_tokens` for the
  current monthly period and compares it to `token_budgets.limit_tokens`. If the total
  already meets the limit, it raises `BudgetExceededError` (429) **before any provider
  call** — a blocked request never spends. The check is *approximate*: it does not
  estimate this call's tokens.
- **Post-call (exact).** On success it appends an immutable `token_usage_events` row and
  **atomically UPSERT-increments** the period row (`INSERT … ON CONFLICT … DO UPDATE SET
  total = total + delta RETURNING total`) in the request's transaction. Never a `SUM`
  over events on the hot path — the materialized row is the O(1) source for the next
  pre-check.
- **Soft threshold.** Crossing `soft_threshold_pct` of the limit flips
  `soft_threshold_crossed` via compare-and-set, so the warn log + audit fire exactly
  once per period regardless of concurrency.

**The chosen semantics:** the hard cap blocks *subsequent* calls. A request already
admitted runs to completion and is recorded exactly, so a single request may push the
period **slightly over** the cap; the next request is then blocked. There is **no
mid-stream truncation** and **no fabricated/short output** — the in-flight call is
honored and metered truthfully.

## Consequences

- A budget can be exceeded by at most one in-flight request's worth of tokens — a
  bounded, auditable overshoot, preferred over cancelling a paid-for, in-progress call.
- The reconciliation invariant holds: `Σ events.total_tokens == period.total_tokens ==
  Σ provider-reported usage`, in aggregate **and** per `trace_id` (tested). Failed or
  fallback-attempt calls meter nothing — only the successful completion's tokens count.
- Concurrent completions stay exact because the increment is a single atomic SQL
  statement (row-locked), not an app-side read-modify-write (tested under `gather`).
- Estimated-pre-check / exact-post-record means budgets are a *spend ceiling with a
  one-request tolerance*, not a hard real-time quota; documented so callers don't expect
  byte-exact cutoffs.
