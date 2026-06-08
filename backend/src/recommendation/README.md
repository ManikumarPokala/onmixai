# Recommendation domain

Single-shot, grounded decision support: given a question and an optional collection scope, the
domain retrieves permission-aware evidence, derives a confidence band from that evidence, and
either returns a **completed** recommendation (a decision + alternatives + caveats +
citation-grounded justifications) or **declines** with a reason. A decline is a first-class,
persisted outcome — never a forced answer on thin evidence, never an empty success.

It consumes only existing ports: the permission-aware retriever (`search.SearchService`, the
only path to chunk content — ADR 0009/0010) and the LLM gateway (`ai`, the only path to a
model — Phase 3). It adds no new retrieval or model surface.

## Public service interface (`service.py`)

`RecommendationService` (6-step service anatomy, patterns §1):

- `create(actor, query, collection_scope, request_id) -> RecommendationResponse` — run the
  pipeline (one use case = one transaction), persist the outcome (completed or declined), audit,
  return the DTO. Exposed at `POST /api/v1/recommendations` (rate-limited).
- `get(actor, id) -> RecommendationResponse` — one recommendation the actor **owns**; a
  non-owner (even same org) or absent id is a 404 (no existence oracle).
- `list(actor, cursor, limit) -> RecommendationPage` — the actor's own recommendations,
  newest-first, keyset-paginated, server-capped at `rec_page_size`.

The decision logic lives in the pipeline (`pipeline.py`, patterns §5) and the pure rules
(`rules.py`); the service orchestrates and persists.

## Invariants

- **Decline-or-cite.** A recommendation is either completed-with-grounded-justifications XOR
  declined. Every justification carries ≥1 citation marker (schema-enforced,
  `extra="forbid"`); phantom markers (citing a source not retrieved) are stripped, and if any
  justification loses *all* its markers the whole recommendation declines — a recommendation can
  never rest on an unsupported claim. Zero fabricated citations ever surface (eval-gated).
- **Confidence is DERIVED FROM RETRIEVAL, never the model's self-report (ADR 0016).** The band
  is computed from the sum of the top-k fused retrieval scores; the `RecommendationOutput` schema
  has **no confidence field at all** — the model structurally cannot influence the band. The
  mapping is monotonic non-decreasing in the evidence statistic (pinned by the
  `test_confidence_property.py` hypothesis suite — Phase-5 exit criterion 1).
- **Decline before spend.** A below-floor or empty retrieval declines (`INSUFFICIENT_EVIDENCE`)
  *before* any generation call — zero model cost, proven by the eval (`gateway_called == False`).
- **Infra failure ≠ decline.** A gateway outage, a persistently schema-invalid output, or a
  budget block is a typed `AppError` that propagates (a retryable/5xx error), never a silent
  decline and never a fabricated default — declines and errors are never conflated (the Phase-4
  split, reused), keeping eval metrics clean.
- **Tenant + owner scoped.** Every query is scoped by `org_id` (RLS + predicate) and reads are
  owner-scoped; the retrieval ACL is re-proven through this surface in the isolation suite (a
  recommendation cannot retrieve or cite a chunk the requester cannot read).

## Known limitations

- **Provisional thresholds.** The band thresholds (`rec_confidence_*` in Settings) and the
  "sum of top-k" signal are provisional and calibrated against stub data, not real-embedding
  eval data — see ADR 0016's open calibration question (quantity-vs-quality; a possible top-1
  quality floor). The eval proves harness + pipeline correctness, not semantic quality (the
  ADR 0013 honesty caveat).
- **Single structured call.** One generation per recommendation (no self-revision, no
  multi-pass) — latency is dominated by that call (`docs/benchmarks/recommendation_latency.md`).
