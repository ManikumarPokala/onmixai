"""Confidence-band monotonicity — Phase-5 exit criterion 1 (hypothesis-driven property).

The confidence band is DERIVED FROM RETRIEVAL EVIDENCE, never from the model's self-report
(ADR 0016). Its single contract is that the mapping is **monotonic non-decreasing** in the
retrieval evidence statistic (the sum of the top-k fused scores): stronger retrieval — higher
scores, more results, or cross-arm corroboration — can never yield a *lower* band
(``None`` < low < medium < high). These properties pin that contract across the whole input
space; the branch-by-branch boundary cases live in ``test_rules.py``.
"""

from hypothesis import given
from hypothesis import strategies as st

from src.recommendation.rules import band_rank, confidence_band_from_scores

_SCORE = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
# Thresholds match Settings' rec_confidence_* defaults (floor ≤ medium ≤ high).
_TOP_K, _HIGH, _MEDIUM, _FLOOR = 5, 0.10, 0.06, 0.03


def _band(scores: list[float]) -> str | None:
    return confidence_band_from_scores(
        scores, top_k=_TOP_K, high=_HIGH, medium=_MEDIUM, floor=_FLOOR
    )


@given(
    base=st.lists(_SCORE, min_size=1, max_size=12),
    deltas=st.lists(_SCORE, min_size=1, max_size=12),
)
def test_band_non_decreasing_under_elementwise_improvement(
    base: list[float], deltas: list[float]
) -> None:
    """Raising any retrieved score (element-wise ≥) never lowers the band."""
    n = min(len(base), len(deltas))
    weaker = base[:n]
    stronger = [weaker[i] + deltas[i] for i in range(n)]
    assert band_rank(_band(stronger)) >= band_rank(_band(weaker))


@given(scores=st.lists(_SCORE, max_size=12), extra=_SCORE)
def test_band_non_decreasing_with_more_evidence(scores: list[float], extra: float) -> None:
    """Adding another retrieved source never lowers the band (corroboration only helps)."""
    assert band_rank(_band([*scores, extra])) >= band_rank(_band(scores))


@given(scores=st.lists(_SCORE, max_size=12), perm_seed=st.randoms(use_true_random=False))
def test_band_is_order_invariant(scores: list[float], perm_seed) -> None:  # type: ignore[no-untyped-def]
    """The band depends only on the multiset of scores, not their order (the statistic is a sum)."""
    shuffled = list(scores)
    perm_seed.shuffle(shuffled)
    assert _band(shuffled) == _band(scores)


@given(scores=st.lists(_SCORE, max_size=12))
def test_band_is_deterministic(scores: list[float]) -> None:
    """Same scores → same band, every time (no hidden state)."""
    assert _band(scores) == _band(list(scores))


@given(scores=st.lists(_SCORE, min_size=1, max_size=12))
def test_decline_exactly_below_floor(scores: list[float]) -> None:
    """The band is ``None`` (decline) iff the evidence statistic is below the floor — the
    decline boundary is precisely the floor, nothing else."""
    stat = sum(sorted(scores, reverse=True)[:_TOP_K])
    assert (_band(scores) is None) == (stat < _FLOOR)


def test_empty_retrieval_is_the_weakest_band() -> None:
    """No evidence is strictly weaker than any non-empty retrieval."""
    assert band_rank(_band([])) == 0
    assert _band([]) is None
