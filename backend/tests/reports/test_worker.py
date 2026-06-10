"""Report worker: queued → ready with structured content + metadata; insufficient-evidence
graph terminal → FAILED with reason; CAS claim is single-winner; the sweeper requeues a dead
worker's claim (and FAILs past the attempt cap); a re-run produces identical content.

The worker uses its own committed sessions, so each test seeds a unique committed org (leftovers
are harmless) rather than the rolled-back db_session used by API tests.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from src.identity.models import Organization, Role, User
from src.identity.repository import OrganizationRepository
from src.identity.service import OrgPolicyService
from src.reports.models import Report, ReportStatus, ReportType
from src.reports.repository import ReportRepository
from src.reports.worker import generate_report, sweep_stuck_reports
from src.search.schemas import SearchQuery, SearchResult, SearchResultItem, SourceAttribution
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.fakes.fake_gateway import FakeGateway


class _FakeRetriever:
    def __init__(self, n_sources: int) -> None:
        self._items = [
            SearchResultItem(
                chunk_id=uuid4(),
                content=f"source {i}",
                score=0.5,
                source=SourceAttribution(
                    document_id=uuid4(), collection_id=uuid4(), filename="d.txt", ref={"page": 1}
                ),
            )
            for i in range(n_sources)
        ]

    async def search(self, actor: Any, query: SearchQuery) -> SearchResult:
        return SearchResult(results=self._items, next_cursor=None)


def _report_json() -> str:
    return json.dumps(
        {"sections": [{"heading": "Overview", "body": "grounded.", "citation_markers": [1]}]}
    )


def _gateway() -> FakeGateway:
    gateway = FakeGateway()
    gateway.queue_completion(text=_report_json())
    return gateway


def _ctx(
    engine: AsyncEngine, settings: Settings, gateway: FakeGateway, sources: int
) -> dict[str, Any]:
    return {
        "sessionmaker": async_sessionmaker(engine, expire_on_commit=False),
        "settings": settings,
        "redis": None,
        "gateway_factory": lambda s: gateway,
        "retriever_factory": lambda s: _FakeRetriever(sources),
        "tenant_lister_factory": lambda s: OrgPolicyService(OrganizationRepository(s)),
    }


async def _seed_report(
    engine: AsyncEngine, *, status: ReportStatus = ReportStatus.QUEUED
) -> tuple[UUID, UUID]:
    org_id, user_id, report_id = uuid4(), uuid4(), uuid4()
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_context(session, org_id)
        session.add(Organization(id=org_id, name="R", slug=f"r-{org_id}"))
        session.add(
            User(
                id=user_id,
                org_id=org_id,
                email=f"u-{user_id}@r.test",
                password_hash="x",
                full_name="U",
                role=Role.OWNER,
            )
        )
        await session.flush()
        session.add(
            Report(
                id=report_id,
                org_id=org_id,
                created_by=user_id,
                report_type=ReportType.EXECUTIVE_SUMMARY,
                title="Q3",
                source_query="summarize Q3",
                status=status,
            )
        )
        await session.commit()
    return org_id, report_id


async def _read(engine: AsyncEngine, org_id: UUID, report_id: UUID) -> Report:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_context(session, org_id)
        report = await ReportRepository(session).get(org_id, report_id)
        assert report is not None
        return report


async def test_queued_report_generates_to_ready(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    org_id, report_id = await _seed_report(app_engine)
    await generate_report(
        _ctx(app_engine, settings, _gateway(), sources=3), str(report_id), str(org_id)
    )

    report = await _read(app_engine, org_id, report_id)
    assert report.status == ReportStatus.READY
    assert report.content is not None and report.content["sections"][0]["heading"] == "Overview"
    assert report.generation_metadata is not None
    assert report.generation_metadata["source_document_ids"]
    assert report.generation_metadata["generated_at"]


async def test_insufficient_evidence_fails_with_reason(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    org_id, report_id = await _seed_report(app_engine)
    # 1 source < report_min_sources (2) → node 1 declines → FAILED(INSUFFICIENT_EVIDENCE)
    await generate_report(
        _ctx(app_engine, settings, _gateway(), sources=1), str(report_id), str(org_id)
    )

    report = await _read(app_engine, org_id, report_id)
    assert report.status == ReportStatus.FAILED
    assert report.failure_reason == "INSUFFICIENT_EVIDENCE"


async def test_claim_is_single_winner(app_engine: AsyncEngine) -> None:
    org_id, report_id = await _seed_report(app_engine)
    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with maker() as s1:
        await set_tenant_context(s1, org_id)
        won_first = await ReportRepository(s1).claim(org_id, report_id, now)
        await s1.commit()
    async with maker() as s2:
        await set_tenant_context(s2, org_id)
        won_second = await ReportRepository(s2).claim(org_id, report_id, now)
        await s2.commit()
    assert won_first is True and won_second is False  # CAS: exactly one claim wins


async def test_sweeper_requeues_then_fails_past_cap(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    org_id, report_id = await _seed_report(app_engine, status=ReportStatus.GENERATING)
    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    stale = datetime.now(UTC) - timedelta(seconds=settings.report_claim_timeout_seconds + 60)

    async with maker() as session:  # stuck claim, attempts below the cap
        await set_tenant_context(session, org_id)
        await session.execute(
            update(Report).where(Report.id == report_id).values(claimed_at=stale, attempt_count=1)
        )
        await session.commit()
    await sweep_stuck_reports(_ctx(app_engine, settings, _gateway(), sources=3))
    assert (await _read(app_engine, org_id, report_id)).status == ReportStatus.QUEUED  # requeued

    async with maker() as session:  # back to a stuck claim, now at the cap
        await set_tenant_context(session, org_id)
        await session.execute(
            update(Report)
            .where(Report.id == report_id)
            .values(
                status=ReportStatus.GENERATING,
                claimed_at=stale,
                attempt_count=settings.report_max_attempts,
            )
        )
        await session.commit()
    await sweep_stuck_reports(_ctx(app_engine, settings, _gateway(), sources=3))
    failed = await _read(app_engine, org_id, report_id)
    assert failed.status == ReportStatus.FAILED and failed.failure_reason is not None


async def test_rerun_produces_identical_content(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    org_id, report_id = await _seed_report(app_engine)
    # Same retrieval (same chunk ids) + deterministic generation across both runs — production
    # re-retrieves the same corpus, so a re-run is content-identical.
    retriever = _FakeRetriever(3)
    maker = async_sessionmaker(app_engine, expire_on_commit=False)

    def ctx() -> dict[str, Any]:
        c = _ctx(app_engine, settings, _gateway(), sources=3)
        c["retriever_factory"] = lambda s: retriever
        return c

    await generate_report(ctx(), str(report_id), str(org_id))
    first = (await _read(app_engine, org_id, report_id)).content

    async with maker() as session:  # requeue and run again
        await set_tenant_context(session, org_id)
        await session.execute(
            update(Report)
            .where(Report.id == report_id)
            .values(status=ReportStatus.QUEUED, claimed_at=None)
        )
        await session.commit()
    await generate_report(ctx(), str(report_id), str(org_id))
    second = (await _read(app_engine, org_id, report_id)).content

    assert first == second  # deterministic content (the hashed report body) is identical


async def test_permanent_errors_mark_failed(app_engine: AsyncEngine, settings: Settings) -> None:
    from src.ai.gateway import BudgetExceededError, GuardrailViolationError, UpstreamRejectedError

    for exc in [
        UpstreamRejectedError(code="POLICY_VIOLATION", message="blocked"),
        BudgetExceededError(message="budget exceeded"),
        GuardrailViolationError(code="GROUNDING_FAILED", message="ungrounded"),
    ]:
        org_id, report_id = await _seed_report(app_engine)
        gateway = FakeGateway()
        gateway.queue_error(exc)

        await generate_report(
            _ctx(app_engine, settings, gateway, sources=3), str(report_id), str(org_id)
        )

        report = await _read(app_engine, org_id, report_id)
        assert report.status == ReportStatus.FAILED
        assert report.failure_reason == exc.code


async def test_transient_upstream_unavailable_error_does_not_mark_failed(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    from src.ai.gateway import UpstreamUnavailableError

    org_id, report_id = await _seed_report(app_engine)
    gateway = FakeGateway()
    gateway.queue_error(UpstreamUnavailableError())

    await generate_report(
        _ctx(app_engine, settings, gateway, sources=3), str(report_id), str(org_id)
    )

    report = await _read(app_engine, org_id, report_id)
    # It was claimed and marked as GENERATING, and stays GENERATING
    assert report.status == ReportStatus.GENERATING
    assert report.failure_reason is None


async def test_sweeper_requeues_stale_queued_reports(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    org_id, report_id = await _seed_report(app_engine, status=ReportStatus.QUEUED)
    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    stale = datetime.now(UTC) - timedelta(seconds=settings.report_claim_timeout_seconds + 60)

    # Make the report created_at stale
    async with maker() as session:
        await set_tenant_context(session, org_id)
        await session.execute(update(Report).where(Report.id == report_id).values(created_at=stale))
        await session.commit()

    # Stub class to track Redis enqueue
    class FakeRedisQueue:
        def __init__(self) -> None:
            self.enqueued: list[tuple[str, str]] = []

        async def enqueue_job(self, task_name: str, report_id_str: str, org_id_str: str) -> None:
            self.enqueued.append((report_id_str, org_id_str))

    fake_redis = FakeRedisQueue()
    ctx = _ctx(app_engine, settings, _gateway(), sources=3)
    ctx["redis"] = fake_redis

    await sweep_stuck_reports(ctx)

    report = await _read(app_engine, org_id, report_id)
    assert report.status == ReportStatus.QUEUED
    assert (str(report_id), str(org_id)) in fake_redis.enqueued
