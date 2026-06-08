"""Reports service (patterns.md §1) — create a QUEUED report and enqueue its generation job
after the row commits (so the worker never races an uncommitted report), and read reports
owner-scoped (a non-owner gets 404, no oracle). Generation itself runs in the worker (Task 6)
via the fixed LangGraph graph (Task 5)."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.schemas import AuthContext
from src.reports.exceptions import ExportNotFoundError, ReportNotFoundError
from src.reports.models import (
    ExportFormat,
    ExportStatus,
    Report,
    ReportExport,
    ReportStatus,
    ReportType,
)
from src.reports.repository import ReportExportRepository, ReportRepository
from src.reports.schemas import ExportResponse, ReportPage, ReportResponse
from src.shared.audit import AuditEmitter
from src.shared.config import Settings
from src.shared.database import register_after_commit
from src.shared.pagination import decode_keyset_cursor, encode_keyset_cursor
from src.shared.queue import JobQueue


class ReportService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: ReportRepository,
        queue: JobQueue,
        audit: AuditEmitter,
        settings: Settings,
    ) -> None:
        self._session = session
        self._repository = repository
        self._queue = queue
        self._audit = audit
        self._settings = settings

    async def create(
        self,
        actor: AuthContext,
        *,
        report_type: ReportType,
        title: str,
        query: str,
        collection_scope: list[UUID],
    ) -> ReportResponse:
        """Create a QUEUED report and enqueue generation after commit. Time: O(1)."""
        report = Report(
            org_id=actor.org_id,
            created_by=actor.user_id,
            report_type=report_type,
            title=title,
            source_query=query,
            collection_scope=[str(c) for c in collection_scope],
            status=ReportStatus.QUEUED,
        )
        await self._repository.add(report)
        org_id, report_id = actor.org_id, report.id
        register_after_commit(
            self._session,
            lambda: self._queue.enqueue_report(report_id=report_id, org_id=org_id),
        )
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="report.created",
            resource_id=report.id,
            report_type=report_type.value,
            query_length=len(query),  # length + scope only — never content
            scope_size=len(collection_scope),
        )
        return ReportResponse.from_model(report)

    async def get(self, actor: AuthContext, report_id: UUID) -> ReportResponse:
        """One report the actor owns. Raises REPORT_NOT_FOUND if absent or owned by another
        user (even same org) — no existence oracle. Time: O(1)."""
        report = await self._repository.get(actor.org_id, report_id)
        if report is None or report.created_by != actor.user_id:
            raise ReportNotFoundError()
        return ReportResponse.from_model(report)

    async def list(self, actor: AuthContext, *, cursor: str | None, limit: int) -> ReportPage:
        """One newest-first page of the actor's reports. Time: O(limit). Raises INVALID_CURSOR
        on a malformed cursor."""
        capped = min(limit, self._settings.report_page_size)
        before = decode_keyset_cursor(cursor) if cursor is not None else None
        rows = await self._repository.list_for_owner(
            actor.org_id, actor.user_id, limit=capped + 1, before=before
        )
        has_more = len(rows) > capped
        page = rows[:capped]
        next_cursor = encode_keyset_cursor(page[-1].created_at, page[-1].id) if has_more else None
        return ReportPage(
            reports=[ReportResponse.from_model(r) for r in page], next_cursor=next_cursor
        )


class ReportExportService:
    """PDF export use cases — idempotent creation (one export per report+format), owner-scoped
    reads, and ACL'd download resolution. Rendering runs in the worker (Task 7)."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        exports: ReportExportRepository,
        reports: ReportRepository,
        queue: JobQueue,
        audit: AuditEmitter,
    ) -> None:
        self._session = session
        self._exports = exports
        self._reports = reports
        self._queue = queue
        self._audit = audit

    async def _owned_report(self, actor: AuthContext, report_id: UUID) -> Report:
        report = await self._reports.get(actor.org_id, report_id)
        if report is None or report.created_by != actor.user_id:
            raise ReportNotFoundError()  # absent or not theirs — no existence oracle
        return report

    async def create(self, actor: AuthContext, report_id: UUID) -> ExportResponse:
        """Create (or return the existing) PDF export for a report the actor owns. Idempotent
        per (report, pdf): a second request returns the in-flight/ready export, not a duplicate.
        Time: O(1)."""
        await self._owned_report(actor, report_id)
        existing = await self._exports.get_for_report(actor.org_id, report_id, ExportFormat.PDF)
        if existing is not None:
            return ExportResponse.from_model(existing)
        export = ReportExport(
            org_id=actor.org_id,
            report_id=report_id,
            format=ExportFormat.PDF,
            status=ExportStatus.QUEUED,
        )
        await self._exports.add(export)
        org_id, export_id = actor.org_id, export.id
        register_after_commit(
            self._session,
            lambda: self._queue.enqueue_export(export_id=export_id, org_id=org_id),
        )
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="report.export_created",
            resource_id=export.id,
        )
        return ExportResponse.from_model(export)

    async def get(self, actor: AuthContext, report_id: UUID, export_id: UUID) -> ExportResponse:
        """One export of a report the actor owns. Raises 404 if the report is not theirs or the
        export is absent / not under that report. Time: O(1)."""
        await self._owned_report(actor, report_id)
        export = await self._exports.get(actor.org_id, export_id)
        if export is None or export.report_id != report_id:
            raise ExportNotFoundError()
        return ExportResponse.from_model(export)

    async def resolve_download(self, actor: AuthContext, report_id: UUID, export_id: UUID) -> str:
        """ACL-check + return the storage key to stream. A non-owner / cross-org / not-ready
        export is a 404 — never another tenant's object. Time: O(1)."""
        await self._owned_report(actor, report_id)
        export = await self._exports.get(actor.org_id, export_id)
        if (
            export is None
            or export.report_id != report_id
            or export.status != ExportStatus.READY
            or export.storage_key is None
        ):
            raise ExportNotFoundError()
        return export.storage_key
