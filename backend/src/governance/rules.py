"""Pure governance rules (no I/O) — branch-testable (patterns.md §4)."""

from datetime import datetime, timedelta

_DEFAULT_WINDOW_DAYS = 30


def resolve_window(
    start: datetime | None, end: datetime | None, *, now: datetime
) -> tuple[datetime, datetime]:
    """Resolve an analytics window: end defaults to ``now``, start to ``end - 30d``. The window
    is half-open [start, end). Time/Space: O(1)."""
    resolved_end = end or now
    resolved_start = start or (resolved_end - timedelta(days=_DEFAULT_WINDOW_DAYS))
    return resolved_start, resolved_end
