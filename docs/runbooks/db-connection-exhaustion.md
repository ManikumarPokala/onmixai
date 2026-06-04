# Runbook — Database connection exhaustion

Symptoms: requests time out or 503; logs show pool checkout timeouts
(`db_pool_timeout_seconds` exceeded) or Postgres `too many clients already`.

## Quick triage

1. **Confirm readiness.** `GET /health/ready` — a 503 with
   `{"checks":{"database":"down"}}` points at the DB/pool, not app logic.
2. **Inspect Postgres connections.**
   `SELECT count(*), state FROM pg_stat_activity GROUP BY state;`
   Look for many `idle in transaction` (a transaction-boundary leak) or total
   connections near `max_connections`.
3. **Check pool config** (`shared/config.py`): `db_pool_size` (10),
   `db_max_overflow` (5), `db_pool_timeout_seconds` (30). Effective per-process
   ceiling ≈ `pool_size + max_overflow`; multiply by worker/replica count and
   compare to Postgres `max_connections`.

## Causes and actions

- **`idle in transaction` buildup** → a code path not closing its session.
  Sessions are owned by the request scope (`get_db_session`: commit/rollback/close).
  Any ad-hoc session creation outside that dependency is the bug — find and remove
  it. As an immediate mitigation, set a Postgres
  `idle_in_transaction_session_timeout`.
- **Too many replicas/workers × pool size > max_connections** → reduce per-process
  pool, lower worker count, or raise Postgres `max_connections` / introduce
  PgBouncer (transaction pooling).
- **Traffic spike** → the upload/API path sheds load (429) under backpressure;
  scale the API horizontally (stateless) and ensure pool math still fits.
- **Stuck connections after a DB restart** → `pool_pre_ping=True` recycles dead
  connections automatically; no API restart required (verified by the
  `docker restart postgres` recovery drill).

## Verify recovery

`pg_stat_activity` connection count returns to baseline; `GET /health/ready` is
200; p95 latency normalizes.
