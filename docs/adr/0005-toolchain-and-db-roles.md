# ADR 0005 — Python Toolchain and Database Role Separation

Status: Accepted (2026-06-04)

## Context

Two standing infrastructure decisions made during Phase 0 deserve recording: how
the pinned Python 3.12 toolchain is provisioned, and how migration vs. runtime
database privileges are separated to make RLS enforceable.

## Decision

### uv-managed Python 3.12

The engineering contract pins Python 3.12 (CLAUDE.md §10). We provision it with
[`uv`](https://docs.astral.sh/uv/): `uv python install 3.12` and a uv-managed venv
at `backend/.venv`. `uv pip install -e ".[dev]"` is the documented equivalent of
the spec's `pip install` (uv venvs do not ship `pip`). The Dockerfile pins
`python:3.12-slim` for parity. Exact dependency versions are pinned in
`pyproject.toml`. Rationale: reproducible, fast, isolated from any system Python
(the host carried only 3.14), and the same toolchain in CI via `astral-sh/setup-uv`.

### Owner / runtime database role separation

Two roles back every environment:

- **Migration owner** (`onmixai`, superuser in dev) — runs Alembic via a separate
  `MIGRATION_DATABASE_URL`. Creates extensions, tables, RLS policies, grants.
- **Application runtime** (`onmixai_app`, `NOSUPERUSER NOBYPASSRLS`) — the role the
  app connects as (`DATABASE_URL`). Because it cannot bypass RLS, tenant isolation
  (ADR 0003) is always enforced for application queries.

Migration 0001 is deliberately **role-agnostic** (schema + RLS only). The runtime
role and its grants are provisioned out of band: an infra init script
(`infra/postgres/initdb/`) in dev, and the testcontainers conftest in CI/test.
`ALTER DEFAULT PRIVILEGES FOR ROLE onmixai` ensures tables created by **future**
migrations are automatically granted to `onmixai_app` — verified empirically, so
migration 0002+ needs no manual grant step.

## Consequences

- The application literally cannot bypass RLS; a superuser connection string in
  the app config would be a reviewable regression.
- Alembic needs elevated privileges, hence the separate `MIGRATION_DATABASE_URL`
  (migration tooling config, read only by `alembic/env.py`, not app `Settings`).
- New developers install one tool (`uv`); CI uses the identical toolchain.
- Dev uses non-default host ports (Postgres 5440, API 8008) to avoid collisions
  with common local services; container-internal ports are unchanged.
