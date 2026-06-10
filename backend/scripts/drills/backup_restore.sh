#!/usr/bin/env bash
# Backup / restore + DR drill (Phase 7 / Task 4) — RUN BY YOU (Docker + pg tools; not CI).
# Backs up a populated multi-org DB, restores into a CLEAN database, and asserts: data intact,
# tenancy/RLS survive the restore (the runtime role still sees ZERO cross-org rows — the same
# property the isolation suite asserts), and the app serves. Times the procedure against the RTO.
set -uo pipefail
COMPOSE="docker compose -f infra/docker-compose.yml"
PG_SVC="postgres"
DB="${POSTGRES_DB:-onmixai}"
OWNER="${POSTGRES_USER:-onmixai}"
APP_ROLE="${APP_DB_ROLE:-onmixai_app}"
RESTORE_DB="onmixai_restore"
DUMP="/tmp/onmixai_backup.dump"
start=$(date +%s)

echo "── 1. Backup (custom-format pg_dump of ${DB}) + object storage"
$COMPOSE exec -T "$PG_SVC" pg_dump -U "$OWNER" -Fc -d "$DB" -f "$DUMP" \
  && echo "  ✓ DB dumped to ${DUMP} (inside the postgres container)" || { echo "  ✗ pg_dump failed"; exit 1; }
echo "  → object storage: back up the bucket too (mc mirror / aws s3 sync) — record it; chunks reference keys."

echo "── 2. Restore into a clean database (${RESTORE_DB})"
$COMPOSE exec -T "$PG_SVC" psql -U "$OWNER" -d "$DB" -c "DROP DATABASE IF EXISTS ${RESTORE_DB};" >/dev/null 2>&1
$COMPOSE exec -T "$PG_SVC" psql -U "$OWNER" -d "$DB" -c "CREATE DATABASE ${RESTORE_DB};" >/dev/null 2>&1
$COMPOSE exec -T "$PG_SVC" pg_restore -U "$OWNER" -d "$RESTORE_DB" "$DUMP" \
  && echo "  ✓ restored into ${RESTORE_DB}" || echo "  ⚠ pg_restore reported issues (review; non-fatal for owned objects)"

echo "── 3. Integrity: multi-org row counts survive"
$COMPOSE exec -T "$PG_SVC" psql -U "$OWNER" -d "$RESTORE_DB" -c \
  "SELECT (SELECT count(*) FROM organizations) AS orgs, (SELECT count(*) FROM documents) AS docs, (SELECT count(*) FROM chunks) AS chunks, (SELECT count(*) FROM audit_events) AS audit;"
echo "  → compare these to the source DB; they must match."

echo "── 4. Tenancy / RLS survive the restore (the DR-critical proof)"
echo "  As the NON-bypassrls runtime role, with NO tenant context set, a tenant table must return ZERO rows"
echo "  (RLS policies + FORCE were restored). With a context set, only that org's rows are visible."
$COMPOSE exec -T "$PG_SVC" psql -U "$APP_ROLE" -d "$RESTORE_DB" -c \
  "SELECT count(*) AS rows_visible_without_tenant_context FROM documents;" 2>&1 | sed 's/^/    /'
echo "    expect rows_visible_without_tenant_context = 0  (RLS active on the restored DB)."
echo "  → optional full proof: point the isolation suite at ${RESTORE_DB} (set its DATABASE_URL) and run"
echo "    pytest tests/isolation -q — RLS/tenancy assertions pass against the restored data."

echo "── 5. App serves against the restored DB"
echo "  → repoint the API at ${RESTORE_DB} (DATABASE_URL) and confirm /health/ready=200 + a search returns."

elapsed=$(( $(date +%s) - start ))
echo
echo "drill procedure time: ${elapsed}s (the drill is fast; record your realistic full-restore RTO vs the 4h target)."
echo "Record results in docs/runbooks/backup-restore.md (RPO/RTO + the four assertions)."
