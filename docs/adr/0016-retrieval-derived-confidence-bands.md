# ADR 0016 — Confidence bands derived from retrieval evidence

Status: Accepted (2026-06-08)

## Context

A recommendation carries a confidence band (high / medium / low) and may decline when the
evidence is too thin. The band must reflect *how well the recommendation is supported*, and
the PRD review flagged the trap: a model's self-reported confidence ("I'm 95% sure") is
uncalibrated noise — LLMs are not calibrated estimators of their own correctness, and asking
for a number invites confident hallucination. The band must come from the **retrieval
evidence the recommendation stands on**, not the model's opinion of itself.

## Decision

**The confidence band is derived from the retrieval relevance signal, and the model cannot
influence it.** Two enforcement layers:

1. **Structural — the model has no confidence field.** `RecommendationOutput` is strict
   (`extra="forbid"`) and declares no confidence/score field. The model *cannot* emit a
   self-confidence value; the band is attached by the pipeline after generation, purely from
   retrieval. (Proven by `test_band_is_derived_from_scores_not_the_model_claim`: the same
   model output — whose text even claims "99% certain" — yields `high` or `low` depending only
   on the retrieval scores.)

2. **The mapping — sum of the top-k fused scores → band.** The signal is the sum of the top-k
   fused retrieval scores (`rec_confidence_top_k`); the mapping is:

   ```
   sum < floor      → None  (decline: INSUFFICIENT_EVIDENCE, before any generation spend)
   sum ≥ floor      → low
   sum ≥ medium     → medium
   sum ≥ high       → high
   ```

   Thresholds (`rec_confidence_floor ≤ medium ≤ high`) live in `Settings`. The mapping is
   **monotonic non-decreasing** in the signal (`None < low < medium < high`) — strictly better
   retrieval never produces a lower band — pinned by a hypothesis property test in two forms
   (element-wise score improvement; an added source). The fused (RRF) score rewards more
   results, higher ranks, and **cross-arm agreement** (a chunk surfaced by both the vector and
   keyword arms scores ~2× higher), so the band rises with corroborated evidence.

## Calibration question — OPEN, to resolve against real-embedding eval data

The chosen signal (sum of top-k fused scores) rewards corroboration but lets **quantity
substitute for quality**: ten tangentially-relevant chunks can sum to the same band as one
authoritative exact match. For an "evidence strength" semantic, a single highly-relevant
source arguably deserves *more* confidence than many weak ones — breadth should not be able to
manufacture "high" confidence on thin-but-numerous evidence.

**Open question (do NOT change now):** should the band additionally gate on the **top-1
(best-match) score** as a quality floor — e.g. `high` requires both a sufficient sum AND a
top-1 above a relevance bar — so breadth alone can't reach the top band? This is a calibration
decision that needs **real-embedding similarity scores on a real corpus** to settle; the
current RRF fused score is rank-based and cannot distinguish "authoritative" from "merely
top-ranked among weak results". It is recorded here as an explicit to-resolve item.

## Consequences

- **Provisional thresholds.** The threshold *values* (`floor/medium/high`) are calibrated to
  the RRF fused-score scale and are **provisional**. The deterministic stub corpus proves the
  *structure* (monotonicity, decline-before-generation, the model-can't-set-the-band property)
  — NOT calibration quality. Re-tune the values (and resolve the top-1 quality-floor question)
  against real-embedding eval data; the same honesty caveat as the retrieval/chat golden sets.
- Declines and the band share one evidence signal, so "decline" is simply "below the band
  floor" — coherent with `should_decline`.
- A future move to real similarity scores (or a top-1 quality floor) is additive: only the
  statistic + thresholds in `rules.py`/`Settings` change; the structural guarantee (no model
  confidence field) and the monotonicity contract are unaffected, and this ADR is updated
  rather than superseded.
