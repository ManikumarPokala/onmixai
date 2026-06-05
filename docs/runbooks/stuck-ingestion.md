# Runbook — Stuck or failing ingestion

Symptoms: documents stay `QUEUED` (never picked up) or `PROCESSING` (never finish),
or a batch of uploads lands in `FAILED`. Status is visible at every stage via
`GET /api/v1/documents/{id}` (`status`, `failure_reason`).

## Quick triage

1. **Is the worker running?**
   `docker compose -f infra/docker-compose.yml ps worker` — it should be `Up`.
   `docker compose ... logs worker` should show
   `Starting worker for 3 functions: ingest_document, cron:sweep_stuck_documents, cron:sweep_storage_outbox`.
   A worker that exits at startup with `EMBEDDING_API_KEY must be set` has no
   embeddings endpoint — set `EMBEDDING_API_KEY`/`EMBEDDING_BASE_URL` (locally the
   dev `embeddings-stub` service provides one; see the README quickstart).
2. **Redis reachable?** ARQ needs Redis (`REDIS_URL`). If Redis is down, jobs are
   never delivered and documents stay `QUEUED`.
3. **Inspect state** (as the org, RLS-scoped):
   ```sql
   SELECT status, count(*) FROM documents GROUP BY status;
   SELECT id, status, attempt_count, claimed_at, failure_reason
     FROM documents WHERE status IN ('processing','failed') ORDER BY claimed_at;
   ```

## Causes and actions

- **`QUEUED` and never moving** → the enqueue or the worker is the problem, not the
  document. Enqueue happens *after* the request commits (post-commit hook); if Redis
  was down at upload, re-enqueue by re-indexing (`POST /documents/{id}/reindex`, READY
  only) or re-uploading. Confirm the worker is consuming (logs show
  `ingest_document(...)`).
- **`PROCESSING` past the deadline (dead worker)** → the sweeper recovers it.
  `sweep_stuck_documents` runs every 5 minutes and re-queues any `PROCESSING` row
  whose `claimed_at` is older than `INGEST_STUCK_AFTER_SECONDS` (default 1800), or
  fails it once `attempt_count` hits `INGEST_MAX_ATTEMPTS`. To recover immediately,
  run the one-shot sweeper:
  ```bash
  docker compose -f infra/docker-compose.yml run --rm \
    -e INGEST_STUCK_AFTER_SECONDS=0 worker python -m src.sweep_once
  ```
  The claim is compare-and-set on `status`, so a revived original worker and the
  sweeper can never both process the same row.
- **`FAILED` with a reason** → expected for bad input. `failure_reason` is
  user-safe (e.g. *"PDF is password-protected"*, *"file is not a readable DOCX"*,
  *"could not determine the text encoding"*). Fix the source document and re-upload.
  A permanent parse/embedding error is never retried.
- **Whole batch `FAILED` with the same upstream reason** → embeddings provider
  outage or a dimension mismatch. Check `EMBEDDING_BASE_URL`/model and that the
  provider's vector width equals `EMBEDDING_DIMENSION`; a mismatch fails fast
  (`EmbeddingDimensionError`) rather than storing wrong-width vectors.

## Verifying recovery

- `GET /documents/{id}` returns `ready`; re-ingest is idempotent — chunk hashes are
  identical to an uninterrupted run (`scripts/drills/kill_drill.py` proves this).
- No orphaned storage objects after deletes: rows in `storage_deletion_outbox` are
  drained by `sweep_storage_outbox`; a non-empty, growing outbox signals a storage
  outage (the `attempts` column surfaces repeatedly-failing keys).

## Knobs (Settings / env)

`INGEST_STUCK_AFTER_SECONDS` (stale-claim threshold), `INGEST_MAX_ATTEMPTS` (retry
cap before terminal FAILED), `INGEST_CHAOS_DELAY_SECONDS` (fault-injection for
drills — a startup error if set with `ENV=prod`).
