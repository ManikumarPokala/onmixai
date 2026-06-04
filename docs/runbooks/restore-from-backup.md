# Runbook — Restore from backup

Target: RPO 24h, RTO 4h (PRD §8). Restores a Postgres backup into a clean
environment.

## Preconditions

- Latest base backup + WAL archive (or daily logical dump) available in object
  storage.
- A clean Postgres 16 instance (empty data directory).
- Access to the migration-owner credentials and the runtime-role provisioning
  script (`infra/postgres/initdb/`).

## Procedure

1. **Provision the instance.** Start Postgres 16 with the `onmixai` owner role and
   `onmixai` database. Run the runtime-role init script so `onmixai_app`
   (NOSUPERUSER, NOBYPASSRLS) exists with default privileges.
2. **Restore data.**
   - Physical: restore the base backup, then replay WAL to the target recovery
     point (point-in-time recovery).
   - Logical: `pg_restore`/`psql` the dump as the owner role.
3. **Reconcile schema version.** Run `alembic current` (with
   `MIGRATION_DATABASE_URL` pointing at the restored DB). If behind head, run
   `alembic upgrade head`. Never edit a merged migration.
4. **Verify RLS is intact.**
   `SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
    WHERE relname IN ('users','refresh_tokens');` → expect `t | t` for both.
5. **Verify the runtime role cannot bypass RLS.** Connect as `onmixai_app` and
   confirm `SELECT rolbypassrls FROM pg_roles WHERE rolname='onmixai_app';` is `f`.
6. **Smoke test.** Point the API at the restored DB; `GET /health/ready` returns
   200; one `register → login → /users/me` round-trip succeeds.

## Rollback

If verification fails, do not route traffic. Discard the instance and restart from
step 1 with the previous known-good backup.

## Notes

- Deleted documents/conversations are purged immediately from active systems and
  removed from backups on rotation (PRD §8) — a restore may resurrect data deleted
  after the backup; honor any outstanding erasure requests post-restore.
