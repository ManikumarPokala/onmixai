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

# Local Postgres
docker compose -f infra/docker-compose.yml up -d postgres

# Quality gate (lint + typecheck + tests)
make verify

# Run the API with autoreload
make dev
```

The backend reads configuration from `backend/.env`
(copy [`backend/.env.example`](backend/.env.example) and fill in values).

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

## Continuous integration

CI is defined in [`.github/workflows/ci.yml`](.github/workflows/ci.yml). Merges to
`main` require all gates green: ruff, `mypy --strict`, import-linter contracts,
tests with ≥80% coverage, the tenant-isolation suite, migration up/down/up,
frontend build, and security scans (gitleaks, pip-audit, npm audit). See
[`CLAUDE.md` §11](CLAUDE.md) for the full policy.
