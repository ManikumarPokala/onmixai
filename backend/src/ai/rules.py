"""Pure budget/period rules — zero I/O (CLAUDE.md §3.1), branch-tested.

The usage period is bucketed monthly (the only BudgetPeriod today); a budget, when
set, compares against that bucket's running total.
"""

from datetime import UTC, datetime


def monthly_period_start(now: datetime) -> datetime:
    """First instant (UTC) of ``now``'s month — the period bucket key. ``now`` must be
    timezone-aware (naive datetimes are banned, CLAUDE.md §4). Time/Space: O(1)."""
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def crossed_soft_threshold(total: int, limit_tokens: int, soft_pct: int) -> bool:
    """Whether ``total`` has reached ``soft_pct`` % of the hard limit. Time/Space: O(1)."""
    return total >= (limit_tokens * soft_pct) // 100
