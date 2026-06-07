# Runbook — Token budget incident

Symptoms: a tenant's AI calls return `429 BUDGET_EXCEEDED`, or a `budget.soft_threshold_crossed`
audit/warn fired. Budgets are enforced in the gateway (CLAUDE.md §6, ADR 0012): the hard
cap blocks the *next* call before any spend; an in-flight call finishes and is recorded
exactly (a request may push slightly over, then the next is blocked — no mid-stream
truncation).

## Quick triage

1. **Confirm it's the budget, not an outage.** `BUDGET_EXCEEDED` (429) = the org's
   period total reached `token_budgets.limit_tokens`. `UPSTREAM_UNAVAILABLE` (503) =
   provider outage (see provider-outage.md).
2. **Read the period total** (as the org, RLS-scoped):
   ```sql
   SELECT tb.limit_tokens, tb.soft_threshold_pct, tup.total_tokens, tup.soft_threshold_crossed
     FROM token_budgets tb
     JOIN token_usage_periods tup ON tup.org_id = tb.org_id
    WHERE tb.org_id = :org AND tup.period_start = date_trunc('month', now());
   ```
3. **Reconcile if disputed** — the materialized total must equal the sum of events:
   ```sql
   SELECT (SELECT total_tokens FROM token_usage_periods
            WHERE org_id = :org AND period_start = date_trunc('month', now())) AS materialized,
          (SELECT coalesce(sum(total_tokens),0) FROM token_usage_events
            WHERE org_id = :org AND created_at >= date_trunc('month', now())) AS from_events;
   ```
   A mismatch is a bug (they are kept equal transactionally — ADR 0012); investigate
   before adjusting anything.

## Causes and actions

- **Legitimately over budget** → raise `token_budgets.limit_tokens` for the org (a
  deliberate, audited change) if the spend is expected; the next call is admitted as
  soon as the total is below the new limit.
- **Spike from one feature** → group `token_usage_events` by `feature` over the period
  to find the source (chat vs report vs eval vs embedding). A runaway feature is a
  product/usage problem, not a metering bug.
- **Soft-warned but not yet blocked** → the soft threshold (default 80%) fired its
  once-per-period warn + audit; this is informational. No action unless the trend will
  exhaust the cap before period end.
- **Per-trace dispute** → every usage event carries `trace_id`; join it to the
  completion trace to audit a single request's exact token attribution.

## Important: what budgets do NOT do

- They are a **spend ceiling with a one-request tolerance**, not a byte-exact real-time
  quota. The overshoot is bounded by a single in-flight call (ADR 0012). Do not expect
  the cap to cut a response mid-stream.
- Failed/fallback attempts meter **nothing** — only successful completions count, so a
  budget can never be consumed by provider errors.

## Knobs (Settings / data)

`token_budgets` (per-org `limit_tokens`, `soft_threshold_pct`, `period`). Usage is
append-only (`token_usage_events`) + the O(1) running total (`token_usage_periods`);
neither is edited by hand outside an audited incident.
