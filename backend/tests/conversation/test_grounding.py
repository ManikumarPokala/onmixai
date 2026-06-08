"""Pure grounding validation: marker validity, phantom stripping, the phantom-fraction
faithfulness rule (refuse a majority-fabricated citation set), phantom_count for the
trace, and tunability of the threshold."""

from src.conversation.grounding import passes_confidence, validate_grounding


def test_passes_confidence() -> None:
    assert passes_confidence(result_count=2, top_score=0.5, min_results=1, min_score=0.0)
    assert not passes_confidence(result_count=0, top_score=0.0, min_results=1, min_score=0.0)
    assert not passes_confidence(result_count=3, top_score=0.2, min_results=1, min_score=0.5)


def test_zero_markers_is_ungrounded() -> None:
    out = validate_grounding("no citations here", num_sources=3, max_phantom_fraction=0.5)
    assert out.refusal_reason == "UNGROUNDED_ANSWER" and out.phantom_count == 0


def test_all_valid_markers_pass() -> None:
    out = validate_grounding("a [1] b [2] c [1]", num_sources=2, max_phantom_fraction=0.5)
    assert out.refusal_reason is None
    assert out.marker_indices == (1, 2) and out.phantom_count == 0
    assert out.text == "a [1] b [2] c [1]"  # unchanged


def test_minority_phantom_is_stripped() -> None:
    out = validate_grounding("a [1] b [2] c [9]", num_sources=2, max_phantom_fraction=0.5)
    assert out.refusal_reason is None
    assert out.marker_indices == (1, 2) and out.phantom_count == 1
    assert "[9]" not in out.text  # phantom stripped, valid kept


def test_phantom_at_parity_is_refused_and_counts_recorded() -> None:
    out = validate_grounding("real [1] fake [9]", num_sources=2, max_phantom_fraction=0.5)
    assert out.refusal_reason == "UNGROUNDED_ANSWER"
    assert out.phantom_count == 1  # invention rate still recorded on refusal
    assert out.marker_indices == ()


def test_threshold_is_tunable() -> None:
    # parity (1 of 2 phantom) is allowed when the threshold is relaxed
    out = validate_grounding("real [1] fake [9]", num_sources=2, max_phantom_fraction=0.9)
    assert out.refusal_reason is None and out.marker_indices == (1,) and out.phantom_count == 1
