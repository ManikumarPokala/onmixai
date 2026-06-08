# Runbook — Stuck or failing report generation / PDF export

Symptoms: a report stays `QUEUED` (never picked up) or `GENERATING` (never finishes), or lands
in `FAILED`; or a PDF export stays `QUEUED`/`GENERATING` or `FAILED` and the Download button
never appears. Status is visible at every stage via `GET /api/v1/reports/{id}`
(`status`, `failure_reason`) and `GET /api/v1/reports/{id}/exports/{export_id}`.

## First: is it a content decline or an infrastructure failure?

These are **different** and must not be confused (the Phase-4 split):

- **Content decline → `FAILED` with a typed reason, by design, not retried.**
  - `INSUFFICIENT_EVIDENCE` — fewer than `report_min_sources` (default 2) permitted chunks were
    retrieved. The report has nothing to stand on. **Action:** widen the collection scope,
    rephrase the query, or ingest documents that cover it. Not a bug.
  - `NO_GROUNDED_SECTIONS` — generation produced sections but every one cited only sources that
    weren't retrieved, so all were dropped. **Action:** same as above; if it recurs on
    answerable queries, treat as a prompt/model regression (run `make eval-report`).
- **Infrastructure failure → a typed `AppError`, retried by the worker, surfaced as `FAILED`
  only after the attempt cap.** This is what the rest of this runbook addresses.

## Quick triage

1. **Is the worker running?**
   `docker compose -f infra/docker-compose.yml ps worker` — it should be `Up`.
   Its startup log lists the registered functions, including `generate_report`,
   `export_report_pdf`, `cron:sweep_stuck_reports`, `cron:sweep_stuck_exports`.
2. **Redis reachable?** ARQ needs Redis (`REDIS_URL`). If Redis was down at request time, the
   post-commit enqueue never delivered and the row stays `QUEUED`.
3. **Storage reachable (exports only)?** The export worker streams the PDF to object storage
   (`STORAGE_*`). If storage is unreachable the export retries, then FAILs at the cap.
4. **Inspect state** (as the org, RLS-scoped):
   ```sql
   SELECT status, count(*) FROM reports GROUP BY status;
   SELECT id, status, attempt_count, claimed_at, failure_reason
     FROM reports WHERE status IN ('generating','failed') ORDER BY claimed_at;
   SELECT id, report_id, status, attempt_count, claimed_at, failure_reason
     FROM report_exports WHERE status IN ('generating','failed') ORDER BY claimed_at;
   ```

## Causes and actions

- **`QUEUED` and never moving** → enqueue or worker problem, not the report. Enqueue runs
  *after* the request commits (post-commit hook); if Redis was down then, the job was never
  delivered. Confirm the worker is consuming (logs show `generate_report(...)` /
  `export_report_pdf(...)`); once Redis/worker are healthy, the sweeper re-enqueues stuck rows
  (below). A brand-new `QUEUED` row with a healthy worker just hasn't been picked up yet.
- **`GENERATING` past the deadline (dead worker)** → the sweeper recovers it. `sweep_stuck_reports`
  and `sweep_stuck_exports` each run every 5 minutes and requeue any `GENERATING` row whose
  `claimed_at` is older than `report_claim_timeout_seconds` (default 600), or mark it `FAILED`
  once `attempt_count` reaches `report_max_attempts` (default 3). The claim is compare-and-set
  on `status`, so a revived original worker and the sweeper can never both process the same row.
- **`FAILED` after the attempt cap (infra)** → the upstream dependency was down for all
  attempts. For reports this is usually the LLM gateway (provider outage / budget hard-cap —
  see `provider-outage.md` / `budget-incident.md`); for exports, object storage. Fix the
  dependency, then re-drive: reports have no in-place "retry" endpoint in V1, so re-create the
  report; exports are idempotent per (report, PDF) — once a prior FAILED export is swept or the
  row is cleared, `POST /reports/{id}/exports` returns the in-flight/ready export, never a
  duplicate.
- **Export FAILED but report is READY** → the report content is fine; only the render/upload
  failed. Re-request the export; the render is deterministic (fixed PDF creation date), so a
  recovered re-export is byte-identical to the original (content-hash asserted in
  `tests/reports/test_export_worker.py`).

## Force immediate recovery

The sweepers run on a 5-minute cron; to recover now, lower the stuck threshold for a one-shot
sweep (compare-and-set keeps this safe even if the original worker revives):
```bash
docker compose -f infra/docker-compose.yml run --rm \
  -e REPORT_CLAIM_TIMEOUT_SECONDS=0 worker python -m src.sweep_once
```

## Verifying recovery

- `GET /reports/{id}` returns `ready` with `sections` + `citations`; or a FAILED report shows an
  honest `failure_reason` (content decline) — never an empty-but-successful report.
- `GET /reports/{id}/exports/{export_id}` returns `ready`; the download streams a valid PDF
  whose citations + generation-metadata footer are present (`scripts/drills/report_export_30pg.sh`
  proves this end-to-end; timing in `docs/benchmarks/report_export.md`).
- The download path is ACL'd: a cross-org / non-owner download is a 404, never another tenant's
  bytes (isolation suite, `tests/isolation/test_reports_isolation.py`).

## Related

- `provider-outage.md`, `budget-incident.md` — the usual upstream causes of report FAILED.
- ADR 0017 (fixed graph, typed terminals), ADR 0018 (PDF library, deterministic render),
  `backend/src/reports/README.md` (the `/CreationDate` ≠ generation-time note).
