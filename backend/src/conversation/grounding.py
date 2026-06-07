"""Pure grounding + confidence rules (no I/O) for the chat pipeline. The cite-or-refuse
invariant lives here: a low-confidence retrieval refuses BEFORE any generation, and a
generated answer must carry valid inline [n] citation markers or it is refused. Phantom
markers (referencing a source that wasn't provided) are stripped; if none survive, the
answer is ungrounded and refused. Branch-tested (patterns.md §4)."""

import re
from dataclasses import dataclass

_MARKER = re.compile(r"\[(\d+)\]")


@dataclass(frozen=True, slots=True)
class GroundingResult:
    refusal_reason: str | None  # set → refuse; None → grounded
    text: str  # answer with phantom markers stripped
    marker_indices: tuple[int, ...]  # validated 1-based source indices actually cited


def passes_confidence(
    *, result_count: int, top_score: float, min_results: int, min_score: float
) -> bool:
    """Whether retrieval is confident enough to generate an answer. Below this the
    pipeline refuses before spending on generation. Time/Space: O(1)."""
    return result_count >= min_results and top_score >= min_score


def validate_grounding(answer: str, *, num_sources: int) -> GroundingResult:
    """Validate inline [n] citation markers against the ``num_sources`` provided sources.

    - zero markers → UNGROUNDED_ANSWER (an answer must cite its sources)
    - markers in 1..num_sources are valid; out-of-range (phantom) markers are stripped
    - if no valid marker survives → UNGROUNDED_ANSWER

    Returns the cleaned text + the sorted unique validated indices. Time: O(len(answer)).
    """
    markers = [int(m) for m in _MARKER.findall(answer)]
    if not markers:
        return GroundingResult("UNGROUNDED_ANSWER", answer, ())

    def _keep_valid(match: re.Match[str]) -> str:
        n = int(match.group(1))
        return match.group(0) if 1 <= n <= num_sources else ""

    cleaned = _MARKER.sub(_keep_valid, answer)
    valid = tuple(sorted({m for m in markers if 1 <= m <= num_sources}))
    if not valid:
        return GroundingResult("UNGROUNDED_ANSWER", cleaned, ())
    return GroundingResult(None, cleaned, valid)
