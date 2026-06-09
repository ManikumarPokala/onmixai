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


def retention_cutoff(window_days: int | None, *, now: datetime) -> datetime | None:
    """The purge cutoff for a retention window: rows strictly older than the returned instant are
    expired. Retain-by-default is the safety invariant — a missing (None) or non-positive
    (zero/negative) window returns None, meaning *retain everything* (the Task-7 job deletes
    nothing). Only a positive day count produces a cutoff. ``now`` must be tz-aware. Time: O(1)."""
    if window_days is None or window_days <= 0:
        return None
    return now - timedelta(days=window_days)
