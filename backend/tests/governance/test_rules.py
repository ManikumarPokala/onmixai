"""Pure governance rules — branch-complete (patterns.md §4)."""

from datetime import UTC, datetime, timedelta

from src.governance.rules import resolve_window

_NOW = datetime(2026, 6, 9, tzinfo=UTC)


def test_defaults_to_last_30_days() -> None:
    start, end = resolve_window(None, None, now=_NOW)
    assert end == _NOW
    assert start == _NOW - timedelta(days=30)


def test_explicit_bounds_are_kept() -> None:
    s, e = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)
    assert resolve_window(s, e, now=_NOW) == (s, e)


def test_end_defaults_to_now_when_only_start_given() -> None:
    s = datetime(2026, 5, 1, tzinfo=UTC)
    assert resolve_window(s, None, now=_NOW) == (s, _NOW)
