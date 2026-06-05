# ADR 0006 — Ingestion Queue: ARQ over Celery

Status: Accepted (2026-06-05)

## Context

Phase 1 ingestion runs asynchronously off the request path (upload → QUEUED →
worker → READY). We needed a job queue + worker for the ingest pipeline and a
periodic sweeper (recover documents abandoned by a dead worker; storage-deletion
compensation). The codebase is async end-to-end (FastAPI + SQLAlchemy asyncio +
asyncpg) and already runs Redis as infrastructure. Requirements: native
`async`/`await` task functions (the pipeline is I/O-bound — storage, DB, embedding
HTTP), scheduled/cron jobs, a small operational surface for a single-team modular
monolith, and easy deterministic testing (call the task function directly with a
fake-populated context).

## Decision

Use **[ARQ](https://arq-docs.helpmanual.io/)** (Redis-backed) as the queue and
worker, not Celery.

- **Async-native.** ARQ task functions are coroutines run on the worker's event
  loop, so the pipeline reuses the exact async repositories, storage adapter, and
  embedder Protocol the API uses — no sync/async bridge. Celery's worker is
  thread/process-based and async support is bolted on; we would be running our
  async stack inside a sync worker.
- **Cron built in.** ARQ's `cron()` covers `sweep_stuck_documents` and
  `sweep_storage_outbox` without adding Celery Beat as a second moving part.
- **Small surface.** One `WorkerSettings` class, Redis we already run, no broker/
  result-backend matrix. Fits a modular monolith maintained by one team.
- **Testable.** `ingest_document(ctx, ...)` is a plain coroutine; tests build `ctx`
  with fakes (storage, embedder, sessionmaker) and call it directly — no broker, no
  Celery test harness. Idempotency, retry, and sweeper paths are unit/integration
  tested deterministically.

## Consequences

- Redis is a hard dependency for ingestion (already provisioned in dev compose and
  expected in every environment). Job durability is Redis-backed; our correctness
  does not rely on it — the DB is the source of truth (CAS claim on `status`,
  content-hash upserts, the stuck-document sweeper, the storage-deletion outbox), so
  a lost Redis job degrades to "document stays QUEUED until re-enqueued/swept", never
  to corruption or duplication.
- ARQ is lighter-weight and less battle-tested at extreme scale than Celery. If a
  future phase needs multi-language workers, complex routing, or very high
  throughput with mature autoscaling, revisit — the worker is isolated behind our
  own `JobQueue` Protocol (`shared/queue.py`), so the broker can be swapped without
  touching domain code.
- CPU-bound parsing runs inside async tasks; pathological documents are bounded
  per-task (page caps, streaming) so one bad file fails its own job, not the worker.
