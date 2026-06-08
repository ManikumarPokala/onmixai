"""PDF export worker: a READY report exports to storage as a PDF whose text carries the
sections, cited sources, and the generation-metadata footer; the export is idempotent (a
re-delivery does not duplicate); the sweeper recovers a dead worker's claim; and a re-render is
byte-identical (deterministic).

Worker tests use committed sessions (unique org per test, leftovers harmless)."""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from src.identity.models import Organization, Role, User
from src.identity.repository import OrganizationRepository
from src.identity.service import OrgPolicyService
from src.reports.export_worker import export_report_pdf, sweep_stuck_exports
from src.reports.models import (
    ExportFormat,
    ExportStatus,
    Report,
    ReportExport,
    ReportStatus,
    ReportType,
)
from src.reports.repository import ReportExportRepository
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.fakes.fake_storage import FakeObjectStorage


def _content() -> dict[str, Any]:
    return {
        "sections": [{"heading": "Overview", "body": "Revenue grew.", "citation_markers": [1]}],
        "citations": [
            {
                "marker_index": 1,
                "chunk_id": str(uuid4()),
                "document_id": str(uuid4()),
                "collection_id": str(uuid4()),
                "filename": "guide.pdf",
                "page_ref": 7,
            }
        ],
    }


def _metadata() -> dict[str, Any]:
    return {
        "model": "openai/stub",
        "prompt_version": "1.0.0",
        "generated_at": "2026-06-08T00:00:00Z",
        "source_document_ids": [str(uuid4())],
    }


async def _seed(engine: AsyncEngine, *, export_status: ExportStatus) -> tuple[UUID, UUID, UUID]:
    org_id, user_id, report_id, export_id = uuid4(), uuid4(), uuid4(), uuid4()
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_context(session, org_id)
        session.add(Organization(id=org_id, name="E", slug=f"e-{org_id}"))
        session.add(
            User(
                id=user_id,
                org_id=org_id,
                email=f"u-{user_id}@e.test",
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
                title="Q3 Review",
                source_query="summarize Q3",
                status=ReportStatus.READY,
                content=_content(),
                generation_metadata=_metadata(),
            )
        )
        session.add(
            ReportExport(
                id=export_id,
                org_id=org_id,
                report_id=report_id,
                format=ExportFormat.PDF,
                status=export_status,
            )
        )
        await session.commit()
    return org_id, report_id, export_id


def _ctx(engine: AsyncEngine, settings: Settings, storage: FakeObjectStorage) -> dict[str, Any]:
    return {
        "sessionmaker": async_sessionmaker(engine, expire_on_commit=False),
        "settings": settings,
        "storage": storage,
        "redis": None,
        "tenant_lister_factory": lambda s: OrgPolicyService(OrganizationRepository(s)),
    }


async def _read_export(engine: AsyncEngine, org_id: UUID, export_id: UUID) -> ReportExport:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_context(session, org_id)
        export = await ReportExportRepository(session).get(org_id, export_id)
        assert export is not None
        return export


async def test_export_renders_pdf_with_citations_and_metadata_footer(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    org_id, _report_id, export_id = await _seed(app_engine, export_status=ExportStatus.QUEUED)
    storage = FakeObjectStorage()
    await export_report_pdf(_ctx(app_engine, settings, storage), str(export_id), str(org_id))

    export = await _read_export(app_engine, org_id, export_id)
    assert export.status == ExportStatus.READY
    assert export.storage_key is not None and export.storage_key in storage.objects

    pdf = storage.objects[export.storage_key]
    assert pdf.startswith(b"%PDF") and len(pdf) > 800  # a real rendered report
    # The cited-sources + metadata-footer TEXT proof is in test_pdf_render.py (PyMuPDF text
    # extraction), which runs in the asyncio-free pass — PyMuPDF segfaults under the
    # pytest-asyncio plugin (ADR 0008). The drill also extracts + asserts the text end-to-end.


async def test_export_is_idempotent(app_engine: AsyncEngine, settings: Settings) -> None:
    org_id, _report_id, export_id = await _seed(app_engine, export_status=ExportStatus.QUEUED)
    storage = FakeObjectStorage()
    ctx = _ctx(app_engine, settings, storage)
    await export_report_pdf(ctx, str(export_id), str(org_id))
    await export_report_pdf(ctx, str(export_id), str(org_id))  # re-delivery — claim now fails

    assert (await _read_export(app_engine, org_id, export_id)).status == ExportStatus.READY
    assert len(storage.objects) == 1  # not duplicated


async def test_rerun_produces_identical_pdf(app_engine: AsyncEngine, settings: Settings) -> None:
    org_id, _report_id, export_id = await _seed(app_engine, export_status=ExportStatus.QUEUED)
    storage = FakeObjectStorage()
    ctx = _ctx(app_engine, settings, storage)
    await export_report_pdf(ctx, str(export_id), str(org_id))
    key = (await _read_export(app_engine, org_id, export_id)).storage_key
    assert key is not None
    first = hashlib.sha256(storage.objects[key]).hexdigest()

    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    async with maker() as session:  # requeue and run again
        await set_tenant_context(session, org_id)
        await session.execute(
            update(ReportExport)
            .where(ReportExport.id == export_id)
            .values(status=ExportStatus.QUEUED, claimed_at=None)
        )
        await session.commit()
    await export_report_pdf(ctx, str(export_id), str(org_id))
    second = hashlib.sha256(storage.objects[key]).hexdigest()
    assert first == second  # deterministic PDF — identical content hash


async def test_sweeper_requeues_then_fails_past_cap(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    org_id, _report_id, export_id = await _seed(app_engine, export_status=ExportStatus.GENERATING)
    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    stale = datetime.now(UTC) - timedelta(seconds=settings.report_claim_timeout_seconds + 60)
    storage = FakeObjectStorage()

    async with maker() as session:
        await set_tenant_context(session, org_id)
        await session.execute(
            update(ReportExport)
            .where(ReportExport.id == export_id)
            .values(claimed_at=stale, attempt_count=1)
        )
        await session.commit()
    await sweep_stuck_exports(_ctx(app_engine, settings, storage))
    assert (await _read_export(app_engine, org_id, export_id)).status == ExportStatus.QUEUED

    async with maker() as session:
        await set_tenant_context(session, org_id)
        await session.execute(
            update(ReportExport)
            .where(ReportExport.id == export_id)
            .values(
                status=ExportStatus.GENERATING,
                claimed_at=stale,
                attempt_count=settings.report_max_attempts,
            )
        )
        await session.commit()
    await sweep_stuck_exports(_ctx(app_engine, settings, storage))
    failed = await _read_export(app_engine, org_id, export_id)
    assert failed.status == ExportStatus.FAILED and failed.failure_reason is not None
