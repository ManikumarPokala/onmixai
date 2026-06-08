"""Reports domain queries. Tenant-scoped (RLS + org_id predicate); per-user ownership is
applied in the service. Status changes use compare-and-set (UPDATE ... WHERE status =
:expected) so concurrent workers/duplicate deliveries can never both claim a report
(patterns.md §3/§7). No business decisions here (CLAUDE.md §3.1)."""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, literal, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.reports.models import Report, ReportStatus


class ReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, report: Report) -> Report:
        """Persist a new (QUEUED) report. Time: O(1)."""
        self._session.add(report)
        await self._session.flush()
        return report

    async def get(self, org_id: UUID, report_id: UUID) -> Report | None:
        result = await self._session.execute(
            select(Report).where(Report.org_id == org_id, Report.id == report_id)
        )
        return result.scalar_one_or_none()

    async def list_for_owner(
        self,
        org_id: UUID,
        created_by: UUID,
        *,
        limit: int,
        before: tuple[datetime, UUID] | None,
    ) -> list[Report]:
        """One newest-first page of the owner's reports via a (created_at, id) keyset cursor.
        Time: O(limit) on ix_reports_org_creator_created."""
        stmt = select(Report).where(Report.org_id == org_id, Report.created_by == created_by)
        if before is not None:
            stmt = stmt.where(
                tuple_(Report.created_at, Report.id)
                < tuple_(literal(before[0]), literal(before[1]))
            )
        stmt = stmt.order_by(Report.created_at.desc(), Report.id.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def claim(self, org_id: UUID, report_id: UUID, now: datetime) -> bool:
        """Atomically claim a QUEUED report (compare-and-set → GENERATING). Returns True iff
        won — exactly one UPDATE matches status='queued'; a duplicate delivery loses. O(1)."""
        stmt = (
            update(Report)
            .where(
                Report.org_id == org_id,
                Report.id == report_id,
                Report.status == ReportStatus.QUEUED,
            )
            .values(
                status=ReportStatus.GENERATING,
                claimed_at=now,
                attempt_count=Report.attempt_count + 1,
            )
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        return result.rowcount == 1

    async def mark_ready(
        self,
        org_id: UUID,
        report_id: UUID,
        *,
        content: dict[str, Any],
        generation_metadata: dict[str, Any],
        trace_id: str | None,
    ) -> bool:
        """GENERATING → READY (compare-and-set), storing content + metadata. O(1)."""
        stmt = (
            update(Report)
            .where(
                Report.org_id == org_id,
                Report.id == report_id,
                Report.status == ReportStatus.GENERATING,
            )
            .values(
                status=ReportStatus.READY,
                content=content,
                generation_metadata=generation_metadata,
                trace_id=trace_id,
                failure_reason=None,
            )
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        return result.rowcount == 1

    async def mark_failed(self, org_id: UUID, report_id: UUID, reason: str) -> bool:
        """GENERATING → FAILED (compare-and-set), recording the user-visible reason. O(1)."""
        stmt = (
            update(Report)
            .where(
                Report.org_id == org_id,
                Report.id == report_id,
                Report.status == ReportStatus.GENERATING,
            )
            .values(status=ReportStatus.FAILED, failure_reason=reason)
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        return result.rowcount == 1

    async def requeue(self, org_id: UUID, report_id: UUID) -> bool:
        """GENERATING → QUEUED (compare-and-set) — the sweeper recovering a dead worker's
        claim. Returns True iff it re-queued. O(1)."""
        stmt = (
            update(Report)
            .where(
                Report.org_id == org_id,
                Report.id == report_id,
                Report.status == ReportStatus.GENERATING,
            )
            .values(status=ReportStatus.QUEUED, claimed_at=None)
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        return result.rowcount == 1

    async def list_stuck(self, org_id: UUID, cutoff: datetime) -> list[Report]:
        """Reports stuck in GENERATING with a claim older than ``cutoff`` (dead worker).
        Time: O(stuck) on ix_reports_status_claimed."""
        result = await self._session.execute(
            select(Report).where(
                Report.org_id == org_id,
                Report.status == ReportStatus.GENERATING,
                Report.claimed_at < cutoff,
            )
        )
        return list(result.scalars().all())
