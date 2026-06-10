"""Report generation worker — idempotent by construction (patterns.md §7).

Claims a QUEUED report with compare-and-set (so duplicate deliveries / two workers never both
run it), runs the fixed knowledge→report graph (Task 5), and writes a user-visible terminal
state: READY with structured content + generation_metadata, or FAILED with a reason (including
the INSUFFICIENT_EVIDENCE / NO_GROUNDED_SECTIONS content declines). An infrastructure failure
leaves the report GENERATING for the sweeper to recover (bounded retries, then FAILED). The
graph's dependencies (retriever, gateway) are composed at the root and passed via the arq ctx.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.ai.gateway import UpstreamUnavailableError
from src.ai.prompt_registry import get_prompt_registry
from src.identity.models import Role
from src.identity.schemas import AuthContext
from src.reports.graph.graph import build_report_graph
from src.reports.repository import ReportRepository
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from src.shared.errors import AppError
from src.shared.queue import REPORT_TASK

_logger = structlog.get_logger("reports.worker")

type SessionMaker = async_sessionmaker[Any]


class TenantLister(Protocol):
    async def all_org_ids(self) -> list[UUID]: ...


async def generate_report(ctx: dict[str, Any], report_id_str: str, org_id_str: str) -> None:
    """Generate one report: claim → run graph → terminal state. Idempotent (CAS claim)."""
    maker: SessionMaker = ctx["sessionmaker"]
    settings: Settings = ctx["settings"]
    report_id, org_id = UUID(report_id_str), UUID(org_id_str)

    # tx1 — claim (commit separately so the claim persists even if generation later fails).
    async with maker() as session:
        await set_tenant_context(session, org_id)
        if not await ReportRepository(session).claim(org_id, report_id, datetime.now(UTC)):
            await session.commit()
            return  # not QUEUED — duplicate delivery or already running
        await session.commit()

    # tx2 — generate + mark terminal.
    async with maker() as session:
        await set_tenant_context(session, org_id)
        repo = ReportRepository(session)
        report = await repo.get(org_id, report_id)
        if report is None:
            await session.commit()
            return
        actor = AuthContext(user_id=report.created_by, org_id=org_id, role=Role.MEMBER)
        graph = build_report_graph(
            retriever=ctx["retriever_factory"](session),
            gateway=ctx["gateway_factory"](session),
            registry=get_prompt_registry(),
            actor=actor,
            settings=settings,
        )
        initial = {
            "query": report.source_query,
            "collection_scope": list(report.collection_scope),
            "report_type": report.report_type.value,
            "request_id": f"report-{report_id}",
        }
        try:
            final: dict[str, Any] = await graph.ainvoke(initial)
        except UpstreamUnavailableError as exc:
            # Infrastructure failure — NOT a content decline. Leave the report GENERATING; the
            # sweeper recovers it (bounded retries, then FAILED). Nothing is marked here.
            _logger.warning(
                "report.infra_failure", report_id=str(report_id), error=type(exc).__name__
            )
            await session.rollback()
            return
        except AppError as exc:
            # Permanent failure (budget, safety rejection, guardrail block). Mark FAILED.
            await repo.mark_failed(org_id, report_id, exc.code)
            await session.commit()
            return

        if final.get("error"):
            await repo.mark_failed(org_id, report_id, final["error"])  # content decline → FAILED
        else:
            content = {"sections": final["sections"], "citations": final["citations"]}
            metadata = {**final["metadata"], "generated_at": datetime.now(UTC).isoformat()}
            await repo.mark_ready(
                org_id,
                report_id,
                content=content,
                generation_metadata=metadata,
                trace_id=final.get("trace_id"),
            )
        await session.commit()


async def sweep_stuck_reports(ctx: dict[str, Any]) -> None:
    """Recover reports stuck in GENERATING past the claim deadline (dead worker): requeue
    (+ re-enqueue) until the attempt cap, then FAIL with a user-visible reason."""
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
            repo = ReportRepository(session)
            for report in await repo.list_stuck(oid, cutoff):
                if report.attempt_count >= settings.report_max_attempts:
                    await repo.mark_failed(oid, report.id, "generation worker died repeatedly")
                    _logger.info("report.sweep_failed", report_id=str(report.id), org_id=str(oid))
                elif await repo.requeue(oid, report.id) and ctx.get("redis") is not None:
                    await ctx["redis"].enqueue_job(REPORT_TASK, str(report.id), str(oid))
                    _logger.info("report.sweep_requeued", report_id=str(report.id), org_id=str(oid))
            await session.commit()
