"""Recommendation rules — branch-complete boundary tests for the confidence band, the decline
gate, and justification grounding.

Confidence is derived from retrieval evidence (ADR 0016); the decline gate fires on
empty/below-floor evidence; justification grounding strips phantom markers and declines when
any claim loses all support. The monotonicity PROPERTY (the band never decreases as retrieval
strengthens) is pinned separately in ``test_confidence_property.py`` — exit criterion 1.
"""

import pytest
from pydantic import ValidationError

from src.recommendation.rules import (
    band_rank,
    confidence_band_from_scores,
    ensure_justifications_grounded,
    should_decline,
)
from src.recommendation.schemas import Justification, RecommendationOutput


def _band(scores: list[float]) -> str | None:
    return confidence_band_from_scores(scores, top_k=5, high=0.10, medium=0.06, floor=0.03)


# --- confidence band + decline: branches ---


def test_empty_retrieval_declines_before_generation() -> None:
    assert _band([]) is None
    assert should_decline([], None) == "INSUFFICIENT_EVIDENCE"


def test_below_floor_declines() -> None:
    band = _band([0.01])  # sum 0.01 < floor 0.03
    assert band is None
    assert should_decline([0.01], band) == "INSUFFICIENT_EVIDENCE"


def test_low_medium_high_band_boundaries() -> None:
    assert _band([0.04]) == "low"  # [floor, medium)
    assert _band([0.06]) == "medium"  # [medium, high)
    assert _band([0.10]) == "high"  # ≥ high
    assert should_decline([0.04], "low") is None  # a real band never declines


def test_evidence_sum_rewards_corroboration() -> None:
    # Two strong cross-arm results outrank a single result → higher band.
    assert band_rank(_band([0.033, 0.033])) >= band_rank(_band([0.016]))


# --- justification grounding ---


def _output(justifications: list[Justification]) -> RecommendationOutput:
    return RecommendationOutput(
        recommendation="Choose A", alternatives=[], justifications=justifications, caveats=[]
    )


def test_strips_phantom_markers_keeps_valid() -> None:
    out = _output([Justification(claim="c", citation_markers=[1, 2, 9])])
    result = ensure_justifications_grounded(out, frozenset({1, 2}))
    assert result.output is not None
    assert result.output.justifications[0].citation_markers == [1, 2]
    assert result.phantom_count == 1  # marker 9 invented


def test_justification_losing_all_markers_invalidates_output() -> None:
    out = _output([Justification(claim="c", citation_markers=[9])])  # only phantom
    result = ensure_justifications_grounded(out, frozenset({1, 2}))
    assert result.output is None  # a claim with no support → decline
    assert result.phantom_count == 1


def test_fully_grounded_output_is_unchanged() -> None:
    out = _output([Justification(claim="c", citation_markers=[1])])
    result = ensure_justifications_grounded(out, frozenset({1, 2}))
    assert result.output is not None and result.phantom_count == 0


# --- schema enforcement ---


def test_schema_rejects_justification_without_markers() -> None:
    with pytest.raises(ValidationError):
        Justification(claim="c", citation_markers=[])  # ≥1 marker required


def test_schema_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        RecommendationOutput(
            recommendation="r",
            alternatives=[],
            justifications=[],
            caveats=[],
            confidence=0.95,  # type: ignore[call-arg]  # model self-report is not a field
        )
