# OnMixAI

Enterprise AI Decision Intelligence Platform. OnMixAI transforms enterprise
knowledge, documents, and operational data into actionable intelligence using
Generative AI, Agentic AI, and Retrieval-Augmented Generation (RAG).

This repository is governed by [`CLAUDE.md`](CLAUDE.md) (the engineering contract)
and the documents under [`docs/`](docs/): the product requirements
([`prd.md`](docs/prd.md)), canonical logic shapes ([`patterns.md`](docs/patterns.md)),
performance standards ([`performance.md`](docs/performance.md)), and the phased
roadmap ([`roadmap.md`](docs/roadmap.md)). The active sprint spec is
[`SPRINT-1.md`](SPRINT-1.md).

## Repository layout

```
onmixai/
├── backend/        FastAPI modular monolith (domains under src/)
├── frontend/       Vite + React + TypeScript
├── infra/          docker-compose for local Postgres + API
├── docs/           PRD, patterns, performance, roadmap, ADRs, runbooks
└── Makefile        developer task runner
```

## Prerequisites

- **Python 3.12** managed via [`uv`](https://docs.astral.sh/uv/)
  (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **Node.js 22+** and npm (frontend)
- **Docker** + Docker Compose (local Postgres)

## Quickstart

```bash
# Backend deps (creates backend/.venv on Python 3.12)
make install

# Backend config: copy the template and adjust if needed
cp backend/.env.example backend/.env

# Backing services (Postgres provisions the runtime role; MinIO = object storage,
# Redis = ingest queue/broker)
docker compose -f infra/docker-compose.yml up -d postgres minio redis
make migrate

# Quality gate (lint + typecheck + tests)
make verify

# Run the full stack: API + ingest worker + Postgres/MinIO/Redis (+ a dev
# embeddings stub so ingestion runs end-to-end with no external account)
docker compose -f infra/docker-compose.yml up -d --build
curl localhost:8008/health      # {"status":"ok"}
```

The backend reads configuration from `backend/.env`
(copy [`backend/.env.example`](backend/.env.example) and fill in values).

### Ingestion worker and embeddings

The `worker` service runs the ARQ ingest pipeline (parse → chunk → embed → store)
plus the stuck-document and storage-deletion sweepers ([ADR 0006](docs/adr/0006-queue-arq-vs-celery.md)).
It embeds via an OpenAI-compatible endpoint: by default it targets the **dev-only
`embeddings-stub`** ([`infra/dev/embeddings_stub.py`](infra/dev/embeddings_stub.py))
which returns deterministic 1536-dim vectors, so the pipeline runs locally with no
API key. Point at a real provider by exporting `EMBEDDING_BASE_URL` /
`EMBEDDING_API_KEY` (its vector width must equal `EMBEDDING_DIMENSION`).

### Local ports (non-default by design)

To avoid colliding with common local services, the dev stack publishes on
non-standard host ports (override with the env vars in parentheses):

| Service | Host port | Container port | Override |
|---|---|---|---|
| Postgres | **5440** | 5432 | `POSTGRES_HOST_PORT` |
| API | **8008** | 8000 | `API_HOST_PORT` |
| MinIO (S3 API / console) | **9110** / **9111** | 9000 / 9001 | `MINIO_API_HOST_PORT` / `MINIO_CONSOLE_HOST_PORT` |
| Redis | **6390** | 6379 | `REDIS_HOST_PORT` |
| embeddings-stub (dev) | **9120** | 8000 | `EMBEDDINGS_STUB_HOST_PORT` |

So health checks are `curl localhost:8008/health` and the DB DSN uses
`localhost:5440`. Inside the compose network services use container names
(`postgres:5432`, `minio:9000`, `redis:6379`).

### Database roles

The app connects as the non-superuser, non-bypassrls runtime role
(`onmixai_app`) so Row-Level Security is always enforced; Alembic migrations run
as the owner role via `MIGRATION_DATABASE_URL`. See
[ADR 0005](docs/adr/0005-toolchain-and-db-roles.md) and
[ADR 0003](docs/adr/0003-tenancy-rls.md).

## Make targets

| Target | Purpose |
|---|---|
| `make install` | Create venv, install backend deps |
| `make dev` | Run the API with autoreload |
| `make test` | Run tests with the ≥80% coverage gate |
| `make lint` | Ruff lint + format check |
| `make typecheck` | `mypy --strict` |
| `make migrate` | Apply DB migrations |
| `make verify` | lint + typecheck + tests |

`make test` runs the suite in two passes via
[`backend/scripts/run-tests.sh`](backend/scripts/run-tests.sh): the async suite,
then the synchronous parser tests in an asyncio-free pass (a PyMuPDF/pytest-asyncio
native conflict — [ADR 0008](docs/adr/0008-parser-test-isolation.md)), with coverage
combined. With the stack up, the Phase-1 exit drills run via
`bash scripts/drills/run_all.sh` (100-page-PDF timing, broken-corpus sweep,
worker-kill idempotency).

## Continuous integration

CI is defined in [`.github/workflows/ci.yml`](.github/workflows/ci.yml). Merges to
`main` require all gates green: ruff, `mypy --strict`, import-linter contracts,
tests with ≥80% coverage, the tenant-isolation suite, migration up/down/up,
frontend build, and security scans (gitleaks, pip-audit, npm audit). See
[`CLAUDE.md` §11](CLAUDE.md) for the full policy.
