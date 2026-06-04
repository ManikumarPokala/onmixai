# ADR 0003 — Multi-Tenancy via Shared Schema + Postgres RLS

Status: Accepted (2026-06-04)

## Context

OnMixAI is multi-tenant. A tenant data leak is a catastrophic failure (PRD Risk 4).
We need tenant isolation that holds even if application code has a bug.

## Decision

**Shared database, shared schema**, with every tenant-owned row carrying
`org_id NOT NULL` (indexed), and defense in depth:

1. **Application scoping** — every repository method touching tenant data takes
   `org_id` explicitly and filters on it. A method without tenant context does not
   exist (CLAUDE.md §4).
2. **Postgres Row-Level Security** — RLS is `ENABLE`d and `FORCE`d on every tenant
   table in the **same migration** that creates the table (migration 0001), with a
   `tenant_isolation` policy keyed on the `app.current_org_id` GUC:
   `org_id = current_setting('app.current_org_id', true)::uuid` (USING + WITH CHECK).
3. **Runtime context** — `set_tenant_context()` sets the GUC per transaction via
   parameter-safe `set_config(..., is_local => true)`; `get_current_user` binds it
   from the verified token before any tenant query.
4. **Non-bypassrls runtime role** — the application connects as `onmixai_app`
   (NOSUPERUSER, NOBYPASSRLS), so RLS is always enforced (ADR 0005).

The `organizations` table itself has no RLS: it is the tenant root and must be
discoverable by slug at login time, before any tenant context exists.

`refresh_tokens` carries `org_id` (beyond the Sprint 1 spec's literal column list)
because CLAUDE.md §4 mandates it on every tenant-owned table and the RLS policy
needs a tenant predicate.

## Consequences

- A cross-tenant read requires *both* an application bug *and* an RLS failure.
- A permanently-blocking isolation test suite proves zero cross-tenant reads as the
  non-bypassrls role, including a raw `SELECT count(*)` that would pass only if RLS
  holds independent of app scoping.
- Tenant context must be set before tenant queries; flows that precede auth (login,
  refresh) resolve the org by slug first, then set the GUC (see ADR 0004).
