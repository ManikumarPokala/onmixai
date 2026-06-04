# OnMixAI — Sprint 1 Specification (Claude Code)

Goal: production-grade foundation. Monorepo, backend core (config, database, RLS, errors, logging, health), Identity domain (orgs, users, JWT auth, RBAC), CI pipeline per CLAUDE.md §10.

Execution rules:
- Complete tasks strictly in order. Each task ends with a **VERIFY** block — run every command. If any fails, fix before moving on. Never proceed on red.
- Comply with `CLAUDE.md` at all times. On conflict, CLAUDE.md wins.
- No placeholder code, no `pass` stubs, no TODOs in committed code.
- Commit after each task passes verification, using Conventional Commits.

Sprint 1 exit criteria (all must hold):
1. `docker compose up` brings up Postgres + API; `/health` and `/health/ready` return 200.
2. Register org+owner → login → refresh → access protected route works end-to-end via tests.
3. Tenant isolation suite proves zero cross-org reads.
4. CI is green on all gates: ruff, mypy --strict, import-linter, tests ≥80% coverage, migration up/down/up, gitleaks, pip-audit.

---

## Task 1 — Monorepo scaffold

Create:

```
onmixai/
├── backend/
│   ├── src/{identity,shared}/        # other domains added in later sprints
│   ├── tests/
│   ├── alembic/
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/                          # Vite + React + TS scaffold only this sprint
├── infra/docker-compose.yml
├── docs/{adr,runbooks}/
├── .github/workflows/ci.yml          # filled in Task 9
├── .gitignore  .env.example  Makefile  CLAUDE.md  README.md
```

`backend/pyproject.toml` — pin exact versions (resolve latest stable at execution time, then pin):
- Runtime: fastapi, uvicorn[standard], sqlalchemy[asyncio]>=2.0, asyncpg, alembic, pydantic>=2, pydantic-settings, structlog, pyjwt, argon2-cffi, python-multipart
- Dev: pytest, pytest-asyncio, pytest-cov, httpx, testcontainers[postgres], ruff, mypy, import-linter, gitleaks via CI, pip-audit

`ruff` config: line-length 100, rules `E,F,I,N,UP,B,C901` (max-complexity 10), format enabled.
`mypy`: strict = true, plugins = pydantic.
`Makefile` targets: `dev`, `test`, `lint`, `typecheck`, `migrate`, `fmt`, `verify` (runs lint+typecheck+test).

**VERIFY**
```
cd backend && pip install -e ".[dev]"
ruff check . && ruff format --check .
mypy src/
cd ../frontend && npm ci && npm run build
```

Commit: `chore: scaffold monorepo with tooling and quality gates`

---

## Task 2 — Typed configuration (shared/config.py)

Single `Settings(BaseSettings)` class. Fields (all typed, no raw `os.getenv` anywhere else in the codebase):

- `env: Literal["dev","test","prod"]`
- `database_url: PostgresDsn`
- `jwt_secret: SecretStr` (min length 32 — validate), `jwt_algorithm: str = "HS256"`
- `access_token_ttl_seconds: int = 900`, `refresh_token_ttl_seconds: int = 1_209_600`
- `log_level: str = "INFO"`
- `db_pool_size: int = 10`, `db_max_overflow: int = 5`, `db_pool_timeout_seconds: int = 30`

Rules:
- `@lru_cache get_settings()` accessor; injected via FastAPI dependency, never imported as a module-level singleton inside business code.
- Fail fast: invalid/missing config raises at startup with a clear message naming the variable. The app must never start half-configured.
- `.env.example` lists every variable with placeholder + comment.

**VERIFY**
```
cd backend
python -c "from src.shared.config import Settings; Settings(_env_file='.env.example')" \
  && echo "config loads"
ENV=prod JWT_SECRET=short python -c "from src.shared.config import get_settings; get_settings()" \
  ; test $? -ne 0 && echo "fail-fast OK"
mypy src/ && ruff check .
```

Commit: `feat(shared): typed fail-fast settings`

---

## Task 3 — Database core + session management (shared/database.py)

- Async engine via `create_async_engine` with pool settings from config, `pool_pre_ping=True` (survives dropped connections — DB restarts must not require an API restart).
- `async_sessionmaker(expire_on_commit=False)`.
- FastAPI dependency `get_db_session`: yields a session, commits on success, rolls back on any exception, always closes. One transaction per request. No sessions created ad hoc elsewhere.
- Tenant context: dependency `get_tenant_session` that, after auth (Task 6), executes `SET LOCAL app.current_org_id = :org_id` on the session so Postgres RLS policies apply. All tenant-data repositories receive sessions only through this dependency.
- Declarative `Base` with naming conventions for constraints/indexes (alembic-stable names).

**VERIFY**
```
docker compose -f infra/docker-compose.yml up -d postgres
cd backend && python -c "
import asyncio
from src.shared.database import engine
from sqlalchemy import text
async def main():
    async with engine.connect() as c:
        assert (await c.execute(text('SELECT 1'))).scalar() == 1
asyncio.run(main()); print('db OK')"
mypy src/ && ruff check .
```

Commit: `feat(shared): async db core with pooled, pre-pinged sessions and tenant context`

---

## Task 4 — Error handling + logging + middleware (shared/errors.py, shared/logging.py, shared/middleware.py)

Errors:
- `AppError(code: str, status: int, message: str, detail: str | None)` base. Subclasses: `NotFoundError(404)`, `ConflictError(409)`, `ValidationFailedError(422)`, `AuthenticationError(401)`, `AuthorizationError(403)`, `RateLimitedError(429)`.
- Global handlers: `AppError` → envelope `{"error":{"code","message","request_id"}}`; `RequestValidationError` → same envelope with field errors; unhandled `Exception` → 500 with generic message, full traceback logged server-side only. Clients never see stack traces, SQL, or internals.

Logging:
- `structlog` JSON renderer; configured once at startup from `log_level`. `print()` is banned (ruff rule T201 — add to config).
- Middleware: generate `request_id` (uuid4) per request, bind to structlog contextvars, return in `X-Request-ID` header; log one line per request (method, path, status, duration_ms, org_id/user_id when authenticated). Expected 4xx logged at INFO, 5xx at ERROR.

**VERIFY**
```
cd backend && pytest tests/shared/test_errors.py tests/shared/test_middleware.py -q
# tests must assert: error envelope shape, request_id present and echoed,
# no traceback text in any client response body, unhandled exception → 500 envelope
mypy src/ && ruff check .
```

Commit: `feat(shared): error envelope, structured logging, request middleware`

---

## Task 5 — Identity domain: models + migration + RLS

`identity/models.py`:
- `organizations`: id (uuid pk), name, slug (unique), created_at.
- `users`: id (uuid pk), org_id (fk, not null, indexed), email (citext, unique per org — composite unique (org_id, email)), password_hash, full_name, role (enum: owner|admin|member), is_active (default true), created_at, updated_at.
- `refresh_tokens`: id (uuid pk), user_id (fk, indexed), token_hash (sha256, unique), expires_at, revoked_at (nullable), created_at. Raw refresh tokens are never stored.

Migration 0001 (alembic):
- Creates extensions (`citext`), tables, indexes.
- Enables RLS on `users` and `refresh_tokens` IN THE SAME MIGRATION:
  `CREATE POLICY tenant_isolation ON users USING (org_id = current_setting('app.current_org_id')::uuid);`
  plus a `FORCE ROW LEVEL SECURITY` so the app role cannot bypass it. A separate migration-owner role runs migrations; the runtime role is non-superuser and non-bypassrls.
- Working `downgrade()` that drops policies, tables, extensions in correct order.

**VERIFY**
```
cd backend
alembic upgrade head
alembic downgrade -1 && alembic upgrade head   # reversibility proven
psql "$DATABASE_URL" -c "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname='users';"
# expect: t | t
```

Commit: `feat(identity): org/user/refresh-token schema with forced RLS in migration 0001`

---

## Task 6 — Identity domain: auth service + repository

`identity/repository.py` (only place with queries):
- `OrganizationRepository`: `create`, `get_by_slug`.
- `UserRepository`: `create`, `get_by_email(org_id, email)`, `get_by_id(org_id, user_id)` — every tenant-data method takes tenant context explicitly; a method without it must not exist.
- `RefreshTokenRepository`: `create`, `get_active_by_hash`, `revoke`, `revoke_all_for_user`.

`identity/service.py`:
- `register_organization(name, slug, owner_email, password, full_name)` — single transaction: create org + owner user. Slug conflict → `ConflictError("ORG_SLUG_TAKEN")`. Password policy: min 12 chars, checked here, not in the router.
- Password hashing: argon2id via `argon2-cffi` with explicit parameters (time_cost=3, memory_cost=64MiB, parallelism=4). Verification is constant-time by library design; on parameter upgrade, rehash transparently at next login.
- `authenticate(org_slug, email, password)` → on success issue access JWT (claims: sub, org_id, role, exp, iat, jti) + opaque refresh token (32 random bytes urlsafe; store sha256 only). Wrong email and wrong password return the identical `AuthenticationError("INVALID_CREDENTIALS")` — no user enumeration. Inactive user → same error.
- `refresh(raw_token)` → rotation: validate hash + expiry + not revoked, revoke old, issue new pair. **Reuse of a revoked refresh token revokes ALL tokens for that user** (token-theft containment) and returns 401.
- `logout(raw_token)` → revoke.

`identity/dependencies.py`:
- `get_current_user`: parse Bearer token, verify signature/exp with leeway=0, load user, assert is_active, bind org_id into tenant session context (Task 3) and structlog context.
- `require_role(*roles)` dependency factory for RBAC.

Hard requirements:
- JWT errors (expired, malformed, bad signature) all map to `AuthenticationError` — never 500.
- Clock: all timestamps UTC, timezone-aware (`datetime.now(timezone.utc)`); naive datetimes are banned in this codebase.

**VERIFY**
```
cd backend && pytest tests/identity/test_service.py -q
# required cases: register happy path; duplicate slug; weak password rejected;
# login wrong password == wrong email (same error code, no enumeration);
# inactive user rejected; refresh rotation works; refresh REUSE revokes all and 401s;
# expired access token rejected; tampered signature rejected
mypy src/ && ruff check .
```

Commit: `feat(identity): auth service with argon2id, rotating refresh tokens, reuse detection`

---

## Task 7 — Identity domain: routes + health endpoints

`identity/router.py` (thin — validate, call one service method, shape response):
- `POST /api/v1/auth/register` → 201 `{organization, user}` (no password fields ever serialized; response schemas exclude them by construction, not by deletion).
- `POST /api/v1/auth/login` → 200 `{access_token, refresh_token, token_type, expires_in}`.
- `POST /api/v1/auth/refresh` → 200 same shape.
- `POST /api/v1/auth/logout` → 204.
- `GET /api/v1/users/me` → 200 current user (protected).
- `GET /api/v1/orgs/me` → 200 current org (protected, `require_role("owner","admin")` for full detail).

Rate limiting on `login` and `refresh`: 10/min per IP+org_slug (slowapi or equivalent middleware) → `RateLimitedError`. Brute force must hit a wall, not the database.

Health (shared/health.py):
- `GET /health` — liveness: process up, 200, no dependencies touched.
- `GET /health/ready` — readiness: `SELECT 1` against DB with a 2s timeout; DB down → 503 with `{"status":"degraded","checks":{"database":"down"}}`. Orchestrators stop routing traffic instead of users seeing 500s.

App factory (`src/main.py`): `create_app()` wiring settings, logging, middleware, exception handlers, routers; lifespan handler disposes the engine on shutdown (clean exits, no dangling connections).

**VERIFY**
```
cd backend && pytest tests/identity/test_api.py tests/shared/test_health.py -q
# required cases: full flow register→login→me→refresh→me→logout→refresh(401);
# 401 envelope on missing/expired token; 403 for member on admin route;
# rate limit returns 429 envelope; /health 200 with DB stopped; /health/ready 503 with DB stopped
docker compose -f infra/docker-compose.yml up -d --build
curl -sf localhost:8000/health && curl -sf localhost:8000/health/ready
```

Commit: `feat(identity): auth routes, RBAC, rate limiting, liveness/readiness probes`

---

## Task 8 — Tenant isolation suite (blocking forever after)

`tests/isolation/test_tenant_isolation.py` against real Postgres (testcontainers), RLS active, runtime (non-bypassrls) role:
1. Create org A and org B, each with users.
2. Through every public repository/service method touching tenant data, assert org A's session can read zero org B rows — by listing, by direct ID (IDOR attempt), and by email lookup.
3. Belt-and-suspenders proof: raw `SELECT count(*) FROM users` under org A context returns only org A's count (RLS working even if app scoping were removed).
4. Token issued for org A user rejected when used after that user is deactivated.

This suite is wired as a separate named CI job. It can never be skipped, marked flaky, or reduced.

**VERIFY**
```
cd backend && pytest tests/isolation/ -q
pytest --cov=src --cov-fail-under=80 -q
```

Commit: `test: tenant isolation suite proving RLS + app scoping`

---

## Task 9 — CI pipeline (.github/workflows/ci.yml)

Jobs (all blocking, per CLAUDE.md §10):
1. **lint**: ruff check + format check
2. **typecheck**: mypy --strict
3. **contracts**: import-linter (identity may import shared; shared imports no domain)
4. **test**: postgres service container → `alembic upgrade head` → pytest with coverage ≥80% → upload coverage artifact
5. **isolation**: tenant isolation suite (separate job, separate name — visible when it fails)
6. **migrations**: clean DB → `upgrade head` → `downgrade base` → `upgrade head`
7. **frontend**: `npm ci`, `tsc --noEmit`, eslint, build
8. **security**: gitleaks (full history), pip-audit (fail on high+), npm audit --audit-level=high
9. Concurrency group per branch (cancel superseded runs); pip/npm caching; pinned action versions (no `@master`).

Branch protection assumption documented in README: merges to main require all 8 jobs green.

**VERIFY**
```
# local dry run of every gate:
cd backend && ruff check . && ruff format --check . && mypy src/ \
  && lint-imports && pytest --cov=src --cov-fail-under=80 -q
cd ../frontend && npx tsc --noEmit && npm run lint && npm run build
# then push branch and confirm all CI jobs green before merging
```

Commit: `ci: full quality-gate pipeline`

---

## Task 10 — Documentation + sprint close

- `docs/adr/0001-modular-monolith.md`, `0002-domain-dependencies.md` (current contract: shared ← identity), `0003-tenancy-rls.md`, `0004-auth-tokens.md` (argon2id params, rotation, reuse-revocation rationale).
- `src/identity/README.md`: responsibility, public service interface, invariants (tenant scoping, no enumeration, rotation), known limitations (no SSO until V2).
- `docs/runbooks/`: `restore-from-backup.md`, `rotate-jwt-secret.md` (dual-secret grace window procedure), `db-connection-exhaustion.md`.
- Root `README.md`: prerequisites, `make dev` quickstart, env setup, test commands, CI overview.

**VERIFY**
```
make verify          # lint + typecheck + full test suite green
docker compose -f infra/docker-compose.yml up -d --build && curl -sf localhost:8000/health/ready
git log --oneline    # conventional commits, one per task
```

Commit: `docs: ADRs, runbooks, domain README, quickstart`

---

## Sprint 1 robustness checklist (final gate — every box ticked before calling the sprint done)

- [ ] App refuses to start on bad config (fail fast, named variable in error)
- [ ] DB restart mid-run: API recovers without restart (pool_pre_ping) — tested manually via `docker restart postgres`
- [ ] DB down: /health 200, /health/ready 503, requests get 503 envelope, zero unhandled tracebacks in logs
- [ ] No client response anywhere contains a stack trace, SQL, or internal path
- [ ] Refresh-token reuse triggers global revocation (test proves it)
- [ ] RLS forced; runtime role cannot bypass; isolation suite green
- [ ] alembic downgrade/upgrade cycle clean on fresh DB
- [ ] Coverage ≥80%; zero ruff warnings; mypy --strict clean; zero `Any` without comment
- [ ] No TODO/FIXME/commented-out code in `git grep` of src/
