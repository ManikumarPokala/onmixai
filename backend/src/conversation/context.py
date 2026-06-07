"""Pure context assembly (zero I/O — the service gathers, the assembler decides;
patterns.md). Packs, in priority order, a system frame → rolling summary → the most
recent history turns → the retrieved (already guardrail-framed) sources, within a token
budget. Trimming is deterministic: history trims first (oldest turns dropped first);
retrieved sources are never trimmed below the confidence floor (``min_sources``). If the
summary + the source floor alone exceed the budget, the floor wins and the budget is
exceeded — grounding is never sacrificed to fit (documented, tested)."""

from dataclasses import dataclass

SYSTEM_FRAME = (
    "Answer using the conversation summary, the recent turns, and the cited sources "
    "below. Cite sources with inline [n] markers; if the sources do not contain the "
    "answer, say so — never invent facts."
)


@dataclass(frozen=True, slots=True)
class HistoryTurn:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class AssembledContext:
    system_frame: str
    summary: str | None
    history: tuple[HistoryTurn, ...]  # included turns, chronological (oldest → newest)
    sources: tuple[str, ...]  # retrieved, framed; best-first ordering preserved
    token_estimate: int
    history_trimmed: int  # turns dropped to fit (trace)
    sources_trimmed: int  # extra sources dropped above the floor (trace)


def _tokens(text: str) -> int:
    """Whitespace-token estimate (the same token model the chunkers use). O(n)."""
    return len(text.split())


def assemble_context(
    *,
    history: list[HistoryTurn],
    summary: str | None,
    sources: list[str],
    budget_tokens: int,
    min_sources: int,
) -> AssembledContext:
    """Pack within ``budget_tokens``. Time: O(t + c) (single pass with precomputed
    counts). Space: O(t + c). t = history turns, c = sources."""
    frame_t = _tokens(SYSTEM_FRAME)
    summary_t = _tokens(summary) if summary else 0
    hist_tokens = [_tokens(turn.content) for turn in history]
    src_tokens = [_tokens(src) for src in sources]

    running = frame_t + summary_t + sum(hist_tokens) + sum(src_tokens)

    # 1. Trim history oldest-first (keep the newest contiguous block).
    dropped_history = 0
    while running > budget_tokens and dropped_history < len(history):
        running -= hist_tokens[dropped_history]
        dropped_history += 1

    # 2. Still over → trim extra sources from the least-relevant end, never below floor.
    kept_sources = len(sources)
    while running > budget_tokens and kept_sources > min_sources:
        kept_sources -= 1
        running -= src_tokens[kept_sources]

    return AssembledContext(
        system_frame=SYSTEM_FRAME,
        summary=summary,
        history=tuple(history[dropped_history:]),
        sources=tuple(sources[:kept_sources]),
        token_estimate=running,
        history_trimmed=dropped_history,
        sources_trimmed=len(sources) - kept_sources,
    )
