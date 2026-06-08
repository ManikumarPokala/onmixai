"""PDF export worker — idempotent by construction (patterns.md §7). Claims a QUEUED export
(CAS), renders the report's structured content to a deterministic PDF (ADR 0018), streams it to
object storage under org/{org_id}/report/{report_id}/{export_id}.pdf, and marks READY with the
storage key. A non-ready report fails the export with a reason; an infrastructure (storage)
failure leaves it GENERATING for the sweeper (bounded retries, then FAILED). The rendered body
is deterministic, so a swept re-export is content-identical."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.reports.models import ReportStatus
from src.reports.pdf import render_report_pdf
from src.reports.repository import ReportExportRepository, ReportRepository
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from src.shared.queue import EXPORT_TASK
from src.shared.storage import ObjectStorage

_logger = structlog.get_logger("reports.export_worker")

type SessionMaker = async_sessionmaker[Any]


class TenantLister(Protocol):
    async def all_org_ids(self) -> list[UUID]: ...


async def _one_chunk(data: bytes) -> AsyncIterator[bytes]:
    yield data


def _storage_key(org_id: UUID, report_id: UUID, export_id: UUID) -> str:
    return f"org/{org_id}/report/{report_id}/{export_id}.pdf"


async def export_report_pdf(ctx: dict[str, Any], export_id_str: str, org_id_str: str) -> None:
    """Render + store one report export. Idempotent (CAS claim)."""
    maker: SessionMaker = ctx["sessionmaker"]
    storage: ObjectStorage = ctx["storage"]
    export_id, org_id = UUID(export_id_str), UUID(org_id_str)

    async with maker() as session:  # tx1 — claim
        await set_tenant_context(session, org_id)
        if not await ReportExportRepository(session).claim(org_id, export_id, datetime.now(UTC)):
            await session.commit()
            return
        await session.commit()

    async with maker() as session:  # tx2 — render + store + mark
        await set_tenant_context(session, org_id)
        exports = ReportExportRepository(session)
        export = await exports.get(org_id, export_id)
        if export is None:
            await session.commit()
            return
        report = await ReportRepository(session).get(org_id, export.report_id)
        if report is None or report.status != ReportStatus.READY or report.content is None:
            await exports.mark_failed(org_id, export_id, "report not ready")
            await session.commit()
            return

        content = report.content
        pdf = render_report_pdf(
            title=report.title,
            sections=content.get("sections", []),
            citations=content.get("citations", []),
            metadata=report.generation_metadata or {},
        )
        key = _storage_key(org_id, report.id, export_id)
        try:
            await storage.put_stream(key, _one_chunk(pdf), "application/pdf")
        except Exception:  # noqa: BLE001 — storage is infrastructure; leave GENERATING for sweep
            _logger.warning("export.storage_failure", export_id=str(export_id))
            await session.rollback()
            return
        await exports.mark_ready(org_id, export_id, storage_key=key)
        await session.commit()


async def sweep_stuck_exports(ctx: dict[str, Any]) -> None:
    """Recover exports stuck in GENERATING past the claim deadline: requeue (+ re-enqueue) until
    the attempt cap, then FAIL with a reason."""
    maker: SessionMaker = ctx["sessionmaker"]
    settings: Settings = ctx["settings"]
    make_lister = ctx["tenant_lister_factory"]
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.report_claim_timeout_seconds)
    async with maker() as session:
        lister: TenantLister = make_lister(session)
        org_ids = await lister.all_org_ids()

    for oid in org_ids:
        async with maker() as session:
            await set_tenant_context(session, oid)
            exports = ReportExportRepository(session)
            for export in await exports.list_stuck(oid, cutoff):
                if export.attempt_count >= settings.report_max_attempts:
                    await exports.mark_failed(oid, export.id, "export worker died repeatedly")
                elif await exports.requeue(oid, export.id) and ctx.get("redis") is not None:
                    await ctx["redis"].enqueue_job(EXPORT_TASK, str(export.id), str(oid))
            await session.commit()
