# ADR 0010 — Permission-Aware Retrieval: the `search` Layer, the Candidate-Reader Port, ACL-in-Predicate

Status: Accepted (2026-06-07)

## Context

Phase 2 adds hybrid retrieval (ADR 0009) over the chunks/embeddings that the
knowledge domain produces. Two design questions had to be settled before any query
was written: **where retrieval lives** relative to knowledge, and **how the ACL is
enforced** so a user can never retrieve a chunk they cannot access — by similarity
search, by keyword, by metadata-filter abuse, or by a guessed chunk id. CLAUDE.md §4
is categorical: "Vector search filters by org_id + collection ACLs inside the SQL
predicate, before similarity ranking. Retrieval without an ACL filter is a security
bug." CLAUDE.md §3.3 requires cross-domain access through a service interface, never
another domain's repository or models.

## Decision

**`search` is its own domain, layered above `knowledge`.** The import-linter stack
is `main > search > knowledge > identity > ai > shared`. `search` owns: query
embedding (via `ai`'s `Embedder` Protocol), RRF fusion (pure, `search/rules.py`),
metadata-filter validation, cursor pagination, source attribution, and the HTTP
entry point (`POST /api/v1/search`). It never imports `knowledge.repository` or
`knowledge.models` — a forbidden import-linter contract enforces this.

**The candidate SQL lives in `knowledge`, behind a port `search` owns (DIP).**
`search` declares a narrow `ChunkCandidateReader` Protocol (`search/ports.py`):
`vector_candidates`, `keyword_candidates`, `candidates_by_ids`. `knowledge`'s
`ChunkRetrievalService` satisfies it **structurally** — the same dependency-inversion
shape knowledge already uses for `OrgQuotaReader`/`TenantLister` (ADR 0002). This
keeps the chunk-candidate SQL (which must touch `chunks`/`documents`/
`collection_permissions` directly) inside the domain that owns those tables, while
`search` depends only on the port and on DTOs. To avoid an upward import, the
candidate DTOs (`ChunkCandidate`, `RetrievalFilters`) are **knowledge-owned**
schemas; knowledge satisfies search's port without importing search.

**ACL in the predicate, before ranking — the only retrieval entry point.** Every arm
builds on one ACL'd candidate `SELECT` (`ChunkRepository._candidate_select`): `org_id
= :org AND embedding IS NOT NULL AND NOT documents.superseded AND EXISTS(collection_
permissions for :user on the chunk's document collection)`, plus any metadata filter,
applied **before** `ORDER BY embedding <=> :q` (vector) / `ts_rank` (keyword). A
metadata filter can only *intersect* the ACL, never widen it. The by-id hydration
path (`candidates_by_ids`) applies the identical predicate, so a known chunk id in an
inaccessible collection — or another org — returns nothing. There is no retrieval
code path that reaches chunk content without this predicate.

**Defense in depth.** The predicate is application-level scoping; underneath, all
five knowledge tables have `org_id NOT NULL` + forced RLS under a non-bypassrls role
(ADR 0003). The isolation suite proves zero cross-org leakage through the vector,
keyword, and by-id arms as that role; the ACL suite proves zero cross-*user* leakage
(no-permission collection) by search, by metadata-filter abuse, and by direct id.

## Consequences

- Retrieval is a thin, testable pipeline: `search` is pure orchestration + fusion
  over a port; the ACL'd SQL is one reusable `_candidate_select` in `knowledge`.
- A new arm or filter cannot bypass the ACL by construction — it composes the same
  `_candidate_select`. Plan-assertion tests (ADR 0009) ensure the arms stay
  index-backed; the isolation + ACL suites are permanently-blocking CI jobs.
- The DIP port means knowledge has no compile-time dependency on search; search can
  be tested with a fake reader, and knowledge with no knowledge of who reads it.
- Cost: the ACL `EXISTS` join is what makes the planner mis-price the vector arm
  (ADR 0009) — the price of enforcing ACL-in-predicate at scale, paid with a
  documented planner coercion rather than by relaxing the security boundary.
