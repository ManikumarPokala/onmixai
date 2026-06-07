# Backlog

Mid-phase ideas captured for later, not built in the phase that surfaced them
(CLAUDE.md §0). Each item names the trigger that would promote it into a sprint.

## Search / retrieval

- **Extended statistics on the chunks ACL-predicate columns (estimate-side fix for
  the HNSW planner fallback).** Source: ADR 0009. The vector arm forces the HNSW
  index with `enable_sort = off` because the planner mis-estimates the ACL join
  (`rows=250` vs actual `100000`) and prices an exact top-N sort as cheaper. That is
  a symptom fix. Evaluate `CREATE STATISTICS` (multi-column / dependency statistics)
  over the columns the ACL predicate joins/filters on (`chunks.org_id`,
  `chunks.document_id` ↔ `documents.collection_id` ↔ `collection_permissions`) so
  the planner prices the join correctly and may choose HNSW unaided. If it does,
  re-test whether `enable_sort = off` is still needed and remove the coercion.
  Promote when: the coercion becomes load-bearing for a new query shape, or a PG/
  pgvector upgrade changes the cost model.

## AI / guardrails

- **Payment-card detection (Luhn-checked) for the PII redactor.** Source: Phase-3
  Task-8 review. Add a `card` type to `PIIRedactor`: a 13–19 digit candidate validated
  by the Luhn checksum (cheap, low false-positive). The per-org flag structure and the
  `{type: int}` counts already accommodate a new type — it's an additive pattern plus a
  validator, no schema change. Promote when: any tenant processes payment-adjacent
  documents, or a compliance requirement (PCI-adjacent) lands.
