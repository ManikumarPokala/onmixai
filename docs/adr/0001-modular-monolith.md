# ADR 0001 — Modular Monolith Architecture

Status: Accepted (2026-06-04)

## Context

OnMixAI spans ten bounded domains (identity, knowledge, search, conversation, AI,
agents, recommendation, reports, governance, admin). We need clear domain
boundaries and an extraction path to services if scale demands it, without paying
the operational and consistency costs of microservices on day one.

## Decision

Build a **modular monolith**: a single deployable backend with domains as bounded
modules under `backend/src/<domain>/`, each owning its `router → service →
repository → rules` layering (CLAUDE.md §3.1). Cross-domain calls go only through
another domain's service interface, never its repository or models (§3.3).
Cross-cutting concerns live in `src/shared/`.

## Consequences

- Single process, single database, transactional consistency between vectors,
  metadata, and ACLs — no distributed transactions in V1.
- Domain boundaries are enforced statically by import-linter (ADR 0002), so the
  monolith does not rot into a big ball of mud.
- If a domain later needs independent scaling, its clean boundary is the seam to
  extract a service along.
- Trade-off: all domains share a deploy and a runtime; a noisy domain can affect
  others until extracted. Accepted for V1.
