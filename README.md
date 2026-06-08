# OnMixAI

OnMixAI is a multi-tenant platform that turns an organization's documents into grounded,
cited answers. It ingests documents, indexes them for hybrid semantic + keyword retrieval,
and exposes that knowledge through grounded chat, structured recommendations, and exportable
reports — with each tenant's data isolated at the database level.

## Features

- **Organizations & auth** — multi-tenant accounts with JWT authentication (short-lived
  access tokens, rotating refresh tokens).
- **Document ingestion** — upload → parse → chunk → embed, processed asynchronously by a
  worker queue, with per-document lifecycle and status.
- **Hybrid retrieval** — vector similarity (pgvector HNSW) combined with full-text search,
  filtered by tenant and per-collection permissions *before* ranking.
- **Grounded chat** — streamed answers that cite their sources or decline when the evidence
  is insufficient; never fabricated.
- **Recommendations** — structured decision output (recommendation, alternatives,
  justifications) with a confidence signal derived from retrieval evidence.
- **Reports** — multi-section, cited reports with PDF export.
- **Tenant isolation** — every tenant-owned table is protected by Postgres row-level security,
  with application-level scoping as defense in depth.

## Architecture

A modular monolith. The backend (FastAPI) is organized by domain — identity, knowledge,
search, conversation, recommendation, reports — each following a `router → service →
repository` layering, with cross-domain calls going through service interfaces.

- **Postgres** (with the `pgvector` extension) is the system of record.
- **Redis** backs the background worker queue (document ingestion, report generation, PDF
  export, and scheduled sweepers).
- An **S3-compatible object store** holds uploaded files and generated exports.
- LLM and embedding calls go through a single gateway behind a provider-agnostic interface,
  so providers can be swapped without touching feature code.

The frontend is a **React + TypeScript** app (Vite) that talks to the backend through a typed
client generated from the API's OpenAPI schema.

## Tech stack

| Area      | Stack |
|-----------|-------|
| Backend   | Python 3.12, FastAPI, SQLAlchemy (async), Alembic, Postgres + pgvector, Redis + ARQ, S3/MinIO |
| Frontend  | React 19, TypeScript, Vite, TanStack Query |
| Tooling   | ruff, mypy, pytest (backend); eslint, vitest (frontend); GitHub Actions CI |

## Prerequisites

- Docker + Docker Compose
- Python 3.12 and [uv](https://github.com/astral-sh/uv) (for local backend work)
- Node.js 22 (for local frontend work)

## Quick start (Docker)

```bash
cp backend/.env.example backend/.env      # review and adjust as needed
docker compose -f infra/docker-compose.yml up --build
```

Default host ports:

| Service        | URL / port |
|----------------|------------|
| API            | http://localhost:8008 |
| Postgres       | localhost:5440 |
| Redis          | localhost:6390 |
| MinIO (S3)     | localhost:9110 (console 9111) |

Check the API is up:

```bash
curl localhost:8008/health     # {"status":"ok"}
```

## Local development

**Backend**

```bash
make install      # create the virtualenv and install dependencies
make migrate      # apply database migrations
make dev          # run the API with autoreload
```

**Frontend**

```bash
cd frontend
npm install
npm run dev       # Vite dev server; proxies /api to the backend on :8008
```

## Tests & quality

**Backend**

```bash
make test         # run the test suite with the coverage gate
make verify       # lint + typecheck + tests (the full local gate)
make lint         # ruff (lint + format check)
make typecheck    # mypy
```

**Frontend**

```bash
cd frontend
npm test          # vitest
npm run lint      # eslint
npm run build     # type-check and production build
```

Continuous integration runs the same lint, type-check, test, migration, and build gates on
every push and pull request (see `.github/workflows/`).

## Project layout

```
backend/    FastAPI application (src/ organized by domain), tests, and database migrations
frontend/   React + Vite single-page app
infra/      docker-compose stack and local development stubs
scripts/    operational and performance drills
```
