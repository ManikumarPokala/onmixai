"""Usage analytics — a read-only aggregation over existing metering/document/audit data. Pure
window resolution lives in rules.py; the aggregates are index-backed repository queries. Reads
are not audited (aggregate, non-sensitive); org-scoped by RLS + the org_id predicate."""

from datetime import UTC, datetime

from src.governance.repository import AnalyticsRepository
from src.governance.rules import resolve_window
from src.governance.schemas import UsageAnalytics
from src.identity.schemas import AuthContext


class AnalyticsService:
    def __init__(self, *, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def usage(
        self,
        actor: AuthContext,
        *,
        start: datetime | None,
        end: datetime | None,
        now: datetime | None = None,
    ) -> UsageAnalytics:
        """One org's usage over the resolved window. Time: O(rows in window) — index-backed,
        never a full scan (plan-asserted)."""
        win_start, win_end = resolve_window(start, end, now=now or datetime.now(UTC))
        org = actor.org_id
        by_feature = await self._repository.tokens_by_feature(org, win_start, win_end)
        doc_count, storage = await self._repository.document_stats(org)
        search_count = await self._repository.action_count(
            org, "search.executed", win_start, win_end
        )
        active = await self._repository.active_user_count(org, win_start, win_end)
        return UsageAnalytics(
            start=win_start,
            end=win_end,
            tokens_total=sum(by_feature.values()),
            tokens_by_feature=by_feature,
            document_count=doc_count,
            storage_bytes=storage,
            search_count=search_count,
            active_users=active,
        )
