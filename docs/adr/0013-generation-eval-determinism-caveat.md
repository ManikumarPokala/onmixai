# ADR 0013 — Generation Eval v0 Measures Harness Correctness, Not Generation Quality

Status: Accepted (2026-06-07)

## Context

Phase 3 ships a generation eval (`make eval-generation`, `tests/eval/test_generation.py`)
that runs the real pipeline — prompt registry → inbound guardrail neutralization →
gateway → `eval_judge_faithfulness@1.0.0` — over a golden set and gates faithfulness
≥ 0.9. But CI has no paid model: the gateway points at the deterministic dev stub. This
is the same honesty problem as the retrieval golden set v0 (ADR 0009/0010): a synthetic
backend can validate plumbing but not quality.

## Decision

**Eval v0 is a harness-correctness gate, not a quality gate.** Against the stub the
judge returns a fixed faithfulness score (1.0), so what the gate actually proves is:

- the full pipeline executes end-to-end for every golden case (registry renders,
  guardrails neutralize, the gateway routes, the judge call validates against the
  faithfulness schema), and
- it is **deterministic** — two runs produce identical scores.

The rubric (`eval_judge_faithfulness`) and threshold (≥ 0.9) are wired now so they
become meaningful the moment a real model is configured, with **no harness change** —
only the `gateway`/model behind it changes. The golden set only grows; it is a permanent
regression gate on the pipeline.

## Consequences

- The reported faithfulness number under CI (1.0) is **not** a quality claim — it is a
  vacuous stub score. Treat a green `eval-generation` as "the generation pipeline is
  wired and deterministic," not "answers are faithful."
- Meaningful faithfulness requires running the same harness against a real model on a
  labeled set — a later, separately-resourced addition. Until then the gate guards
  regressions in the pipeline (prompts, guardrails, gateway, judge schema), which is
  exactly what a `src/ai/**` path-triggered job should catch.
- This mirrors the retrieval blind spot: synthetic data gates plumbing; real quality is
  a deferred, explicit follow-up — never silently assumed from a green CI run.
