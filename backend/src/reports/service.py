"""Reports service (patterns.md §1) — create a QUEUED report and enqueue its generation job
after the row commits (so the worker never races an uncommitted report), and read reports
owner-scoped (a non-owner gets 404, no oracle). Generation itself runs in the worker (Task 6)
via the fixed LangGraph graph (Task 5)."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.schemas import AuthContext
from src.reports.exceptions import ReportNotFoundError
from src.reports.models import Report, ReportStatus, ReportType
from src.reports.repository import ReportRepository
from src.reports.schemas import ReportPage, ReportResponse
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
