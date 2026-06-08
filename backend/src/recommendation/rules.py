"""Pure recommendation rules (no I/O) — the confidence band, the decline gate, and
justification grounding. Each is independently branch-testable (patterns.md §4); thresholds
are passed in by the service from Settings (no magic numbers here).

The confidence band is DERIVED FROM RETRIEVAL EVIDENCE, never from anything the model claims
about its own confidence (ADR 0016): a model saying "95% sure" is uncalibrated noise. The
signal is the sum of the top-k fused retrieval scores — higher with more results, higher ranks,
and cross-arm agreement (a chunk found by both the vector and keyword arms scores ~2× higher).
The mapping is monotonic non-decreasing in that signal (the Task-9 property test pins this).
"""

from dataclasses import dataclass
from typing import Literal

from src.recommendation.schemas import Justification, RecommendationOutput

DeclineReason = Literal["INSUFFICIENT_EVIDENCE"]

# Ordering for monotonicity reasoning: a decline (None) is strictly weakest.
_BAND_RANK: dict[str | None, int] = {None: 0, "low": 1, "medium": 2, "high": 3}


def evidence_score(scores: list[float], *, top_k: int) -> float:
    """The retrieval evidence statistic: the sum of the top-k fused scores. Non-decreasing in
    every input score and in the number of results — more/stronger/cross-arm-corroborated
    evidence raises it. Time: O(n log n) over n results. Space: O(n)."""
    return sum(sorted(scores, reverse=True)[:top_k])


def confidence_band_from_scores(
    scores: list[float], *, top_k: int, high: float, medium: float, floor: float
) -> str | None:
    """Map the retrieval evidence statistic to a confidence band, or ``None`` when the evidence
    is below the decline floor (the caller declines). Monotonic: a statistic that is greater or
    equal never yields a lower band (``None`` < low < medium < high). Time: O(n log n).

    Thresholds satisfy ``floor ≤ medium ≤ high``."""
    if not scores:
        return None
    stat = evidence_score(scores, top_k=top_k)
    if stat < floor:
        return None
    if stat >= high:
        return "high"
    if stat >= medium:
        return "medium"
    return "low"


def band_rank(band: str | None) -> int:
    """Total order over bands (decline=0 < low < medium < high) — for the monotonicity test."""
    return _BAND_RANK[band]


def should_decline(scores: list[float], band: str | None) -> DeclineReason | None:
    """Decline (before any generation spend) when retrieval is empty or the band is below the
    floor — `INSUFFICIENT_EVIDENCE`. Mirrors the chat low-confidence refusal. Time: O(1)."""
    if not scores or band is None:
        return "INSUFFICIENT_EVIDENCE"
    return None


@dataclass(frozen=True, slots=True)
class GroundingResult:
    """The recommendation after grounding. ``output`` is None when a justification lost all of
    its citations — a claim with no support invalidates the whole recommendation (decline)."""

    output: RecommendationOutput | None
    phantom_count: int  # citation markers that referenced a non-existent source (invention rate)


def ensure_justifications_grounded(
    output: RecommendationOutput, valid_markers: frozenset[int]
) -> GroundingResult:
    """Strip every justification's phantom markers (citing a source not retrieved). If ANY
    justification loses all its markers, the recommendation cannot stand — return ``output=None``
    (the caller declines). Otherwise return the cleaned output and the phantom count.
    Time: O(j·m) over justifications × markers. Space: O(j·m)."""
    phantom = 0
    any_emptied = False
    cleaned: list[Justification] = []
    for justification in output.justifications:
        kept = [m for m in justification.citation_markers if m in valid_markers]
        phantom += len(justification.citation_markers) - len(kept)
        if not kept:
            any_emptied = True
        else:
            cleaned.append(Justification(claim=justification.claim, citation_markers=kept))
    if any_emptied:
        return GroundingResult(None, phantom)
    return GroundingResult(output.model_copy(update={"justifications": cleaned}), phantom)
