"""Pure context-assembly properties: never exceeds budget when feasible, the source
floor is always respected, trimming is deterministic (history first, oldest-first), and
an absent/stale summary degrades gracefully."""

from src.conversation.context import (
    SYSTEM_FRAME,
    AssembledContext,
    HistoryTurn,
    assemble_context,
)


def _turns(n: int) -> list[HistoryTurn]:
    # Each turn is ~5 tokens; oldest first.
    return [
        HistoryTurn("user" if i % 2 == 0 else "assistant", f"turn number {i} here")
        for i in range(n)
    ]


def _assemble(
    budget: int,
    *,
    history: list[HistoryTurn],
    summary: str | None,
    sources: list[str],
    min_sources: int = 1,
) -> AssembledContext:
    return assemble_context(
        history=history,
        summary=summary,
        sources=sources,
        budget_tokens=budget,
        min_sources=min_sources,
    )


def test_fits_within_budget_and_keeps_everything_when_room() -> None:
    out = _assemble(
        1000, history=_turns(4), summary="a short summary", sources=["src one", "src two"]
    )
    assert out.token_estimate <= 1000
    assert len(out.history) == 4 and out.history_trimmed == 0
    assert len(out.sources) == 2 and out.sources_trimmed == 0
    assert out.system_frame == SYSTEM_FRAME


def test_history_trims_first_oldest_first() -> None:
    history = _turns(10)
    # Budget fits the frame + sources + only the newest few turns.
    out = _assemble(40, history=history, summary=None, sources=["src one two three"])
    assert out.token_estimate <= 40
    assert out.history_trimmed > 0
    assert out.sources_trimmed == 0  # sources untouched while history can still trim
    # the kept turns are the NEWEST contiguous block
    assert out.history == tuple(history[out.history_trimmed :])


def test_source_floor_respected_even_when_over_budget() -> None:
    # Tiny budget that can't fit the frame + the single source; the floor still keeps it.
    out = _assemble(
        1, history=_turns(6), summary="summary text", sources=["a", "b", "c"], min_sources=2
    )
    assert len(out.sources) == 2  # floor kept, extras dropped
    assert out.history == ()  # all history trimmed first
    assert out.sources_trimmed == 1


def test_absent_summary_leaves_more_room_for_history() -> None:
    history = _turns(6)
    with_summary = _assemble(45, history=history, summary="x " * 20, sources=["s one"])
    without_summary = _assemble(45, history=history, summary=None, sources=["s one"])
    assert without_summary.history_trimmed <= with_summary.history_trimmed
    assert without_summary.summary is None


def test_deterministic() -> None:
    a = _assemble(50, history=_turns(8), summary="sum", sources=["one two", "three four"])
    b = _assemble(50, history=_turns(8), summary="sum", sources=["one two", "three four"])
    assert a == b
