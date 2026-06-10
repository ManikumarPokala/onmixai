"""Reports domain queries. Tenant-scoped (RLS + org_id predicate); per-user ownership is
applied in the service. Status changes use compare-and-set (UPDATE ... WHERE status =
:expected) so concurrent workers/duplicate deliveries can never both claim a report
(patterns.md §3/§7). No business decisions here (CLAUDE.md §3.1)."""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, literal, or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.reports.models import ExportFormat, ExportStatus, Report, ReportExport, ReportStatus


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
        """GENERATING or QUEUED → QUEUED (compare-and-set) — the sweeper recovering a dead worker's
        claim. Returns True iff it re-queued. O(1)."""
        stmt = (
            update(Report)
            .where(
                Report.org_id == org_id,
                Report.id == report_id,
                or_(
                    Report.status == ReportStatus.GENERATING,
                    Report.status == ReportStatus.QUEUED,
                ),
            )
            .values(status=ReportStatus.QUEUED, claimed_at=None)
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        return result.rowcount == 1

    async def list_stuck(self, org_id: UUID, cutoff: datetime) -> list[Report]:
        """Reports stuck in GENERATING or QUEUED past the cutoff.
        Time: O(stuck) on ix_reports_status_claimed."""
        result = await self._session.execute(
            select(Report).where(
                Report.org_id == org_id,
                or_(
                    (Report.status == ReportStatus.GENERATING) & (Report.claimed_at < cutoff),
                    (Report.status == ReportStatus.QUEUED) & (Report.created_at < cutoff),
                ),
            )
        )
        return list(result.scalars().all())


class ReportExportRepository:
    """Report-export queries (idempotent per report+format, CAS lifecycle)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, export: ReportExport) -> ReportExport:
        """Persist a new (QUEUED) export. Time: O(1)."""
        self._session.add(export)
        await self._session.flush()
        return export

    async def get(self, org_id: UUID, export_id: UUID) -> ReportExport | None:
        result = await self._session.execute(
            select(ReportExport).where(ReportExport.org_id == org_id, ReportExport.id == export_id)
        )
        return result.scalar_one_or_none()

    async def get_for_report(
        self, org_id: UUID, report_id: UUID, export_format: ExportFormat
    ) -> ReportExport | None:
        """The existing export for (report, format) — drives request idempotency. O(1)."""
        result = await self._session.execute(
            select(ReportExport).where(
                ReportExport.org_id == org_id,
                ReportExport.report_id == report_id,
                ReportExport.format == export_format,
            )
        )
        return result.scalar_one_or_none()

    async def claim(self, org_id: UUID, export_id: UUID, now: datetime) -> bool:
        """Atomically claim a QUEUED export (compare-and-set → GENERATING). O(1)."""
        stmt = (
            update(ReportExport)
            .where(
                ReportExport.org_id == org_id,
                ReportExport.id == export_id,
                ReportExport.status == ExportStatus.QUEUED,
            )
            .values(
                status=ExportStatus.GENERATING,
                claimed_at=now,
                attempt_count=ReportExport.attempt_count + 1,
            )
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        return result.rowcount == 1

    async def mark_ready(self, org_id: UUID, export_id: UUID, *, storage_key: str) -> bool:
        """GENERATING → READY (compare-and-set), recording the storage key. O(1)."""
        stmt = (
            update(ReportExport)
            .where(
                ReportExport.org_id == org_id,
                ReportExport.id == export_id,
                ReportExport.status == ExportStatus.GENERATING,
            )
            .values(status=ExportStatus.READY, storage_key=storage_key, failure_reason=None)
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        return result.rowcount == 1

    async def mark_failed(self, org_id: UUID, export_id: UUID, reason: str) -> bool:
        """GENERATING → FAILED (compare-and-set). O(1)."""
        stmt = (
            update(ReportExport)
            .where(
                ReportExport.org_id == org_id,
                ReportExport.id == export_id,
                ReportExport.status == ExportStatus.GENERATING,
            )
            .values(status=ExportStatus.FAILED, failure_reason=reason)
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        return result.rowcount == 1

    async def requeue(self, org_id: UUID, export_id: UUID) -> bool:
        """GENERATING or QUEUED → QUEUED (compare-and-set) — sweeper recovery. O(1)."""
        stmt = (
            update(ReportExport)
            .where(
                ReportExport.org_id == org_id,
                ReportExport.id == export_id,
                or_(
                    ReportExport.status == ExportStatus.GENERATING,
                    ReportExport.status == ExportStatus.QUEUED,
                ),
            )
            .values(status=ExportStatus.QUEUED, claimed_at=None)
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        return result.rowcount == 1

    async def list_stuck(self, org_id: UUID, cutoff: datetime) -> list[ReportExport]:
        """Exports stuck in GENERATING or QUEUED with a claim older than ``cutoff``. O(stuck)."""
        result = await self._session.execute(
            select(ReportExport).where(
                ReportExport.org_id == org_id,
                or_(
                    (ReportExport.status == ExportStatus.GENERATING)
                    & (ReportExport.claimed_at < cutoff),
                    (ReportExport.status == ExportStatus.QUEUED)
                    & (ReportExport.created_at < cutoff),
                ),
            )
        )
        return list(result.scalars().all())
