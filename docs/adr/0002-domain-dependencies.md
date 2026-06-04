# ADR 0002 — Domain Dependency Directions

Status: Accepted (2026-06-04)

## Context

A modular monolith (ADR 0001) only stays modular if dependency directions are
enforced. Without enforcement, domains start importing each other's internals and
the boundaries erode.

## Decision

Dependencies flow in one direction: the composition root (`src.main`) may import
domains; domains may import `src.shared`; `src.shared` imports no domain. Domains
do not import each other's internals — cross-domain access is via the other
domain's service interface only.

This is enforced in CI by **import-linter** (`[tool.importlinter]` in
`backend/pyproject.toml`) with a layered contract:

```
layers = ["src.main", "src.identity", "src.shared"]
```

Current contract: `shared ← identity ← main`. As new domains are added they join
the appropriate layer; sibling domains must not import each other (a future
`independence` contract will assert this once a second domain exists).

## Consequences

- A violating import fails the `contracts` CI job — the boundary is a build gate,
  not a convention.
- `shared/` stays free of domain knowledge and is safe for every domain to depend
  on.
- Circular dependencies are a design error surfaced immediately, not at runtime.
