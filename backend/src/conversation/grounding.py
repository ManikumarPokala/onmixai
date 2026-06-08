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
    phantom_count: int  # marker occurrences citing a non-existent source (invention rate)


def passes_confidence(
    *, result_count: int, top_score: float, min_results: int, min_score: float
) -> bool:
    """Whether retrieval is confident enough to generate an answer. Below this the
    pipeline refuses before spending on generation. Time/Space: O(1)."""
    return result_count >= min_results and top_score >= min_score


def validate_grounding(
    answer: str, *, num_sources: int, max_phantom_fraction: float
) -> GroundingResult:
    """Validate inline [n] citation markers against the ``num_sources`` provided sources.

    - zero markers → UNGROUNDED_ANSWER (an answer must cite its sources)
    - a marker is valid iff 1 ≤ n ≤ num_sources; others are phantom (cite a source that
      wasn't provided — a faithfulness failure)
    - if the phantom FRACTION of all markers reaches ``max_phantom_fraction`` (default 0.5
      = parity with real ones), the answer is majority-fabricated → UNGROUNDED_ANSWER
    - otherwise phantom markers are stripped and the surviving valid set is cited

    Returns the cleaned text, the validated indices, and the phantom occurrence count (for
    the trace / citation-precision eval). Time: O(len(answer)). Space: O(markers).
    """
    markers = [int(m) for m in _MARKER.findall(answer)]
    if not markers:
        return GroundingResult("UNGROUNDED_ANSWER", answer, (), 0)

    phantom_count = sum(1 for n in markers if not 1 <= n <= num_sources)
    if phantom_count / len(markers) >= max_phantom_fraction:
        # The model fabricated at least as many citations as it grounded — refuse rather
        # than serve a majority-fabricated citation set (ADR 0014).
        return GroundingResult("UNGROUNDED_ANSWER", answer, (), phantom_count)

    def _keep_valid(match: re.Match[str]) -> str:
        n = int(match.group(1))
        return match.group(0) if 1 <= n <= num_sources else ""

    cleaned = _MARKER.sub(_keep_valid, answer)
    valid = tuple(sorted({n for n in markers if 1 <= n <= num_sources}))
    if not valid:
        return GroundingResult("UNGROUNDED_ANSWER", cleaned, (), phantom_count)
    return GroundingResult(None, cleaned, valid, phantom_count)
