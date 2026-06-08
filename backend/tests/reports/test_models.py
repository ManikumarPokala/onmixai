"""Reports schema: forced RLS on both tenant tables, runtime-role CRUD across the report →
export chain (lifecycle status + metadata), the (report_id, format) uniqueness, and cross-org
RLS hiding."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.service import AuthService
from src.reports.models import (
    ExportFormat,
    ExportStatus,
    Report,
    ReportExport,
    ReportStatus,
    ReportType,
)
from src.shared.database import set_tenant_context

_TABLES = ("reports", "report_exports")


@pytest.mark.parametrize("table", _TABLES)
async def test_forced_rls_enabled_on_reports_tables(db_session: AsyncSession, table: str) -> None:
    row = (
        await db_session.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = :t AND relkind = 'r'"
            ),
            {"t": table},
        )
    ).one()
    assert row == (True, True)  # RLS enabled AND forced (CLAUDE.md §4)


async def _seed_report(db_session: AsyncSession, auth_service: AuthService, slug: str) -> Report:
    org = await auth_service.register_organization(
        name=slug,
        slug=slug,
        owner_email=f"o@{slug}.test",
        full_name="O",
        password="password-123456",
    )
    org_id, user_id = org.organization.id, org.owner.id
    await set_tenant_context(db_session, org_id)
    report = Report(
        org_id=org_id,
        created_by=user_id,
        report_type=ReportType.EXECUTIVE_SUMMARY,
        title="Q3 summary",
        source_query="summarize Q3",
        status=ReportStatus.QUEUED,
    )
    db_session.add(report)
    await db_session.flush()
    return report


async def test_runtime_role_crud_report_and_export(
    auth_service: AuthService, db_session: AsyncSession
) -> None:
    report = await _seed_report(db_session, auth_service, "rep-org")
    report.status = ReportStatus.READY
    report.content = {"sections": [{"heading": "Overview", "body": "…", "citation_markers": [1]}]}
    report.generation_metadata = {
        "model": "openai/gpt-4o-mini",
        "prompt_version": "1.0.0",
        "generated_at": "2026-06-08T00:00:00Z",
        "source_document_ids": [str(report.id)],
    }
    db_session.add(
        ReportExport(
            org_id=report.org_id,
            report_id=report.id,
            format=ExportFormat.PDF,
            status=ExportStatus.QUEUED,
        )
    )
    await db_session.flush()

    ready = (
        await db_session.execute(
            text("SELECT status, content, generation_metadata FROM reports WHERE id = :i"),
            {"i": str(report.id)},
        )
    ).one()
    assert ready.status == "ready"
    assert ready.content["sections"][0]["heading"] == "Overview"
    assert ready.generation_metadata["source_document_ids"] == [str(report.id)]


async def test_export_is_unique_per_report_and_format(
    auth_service: AuthService, db_session: AsyncSession
) -> None:
    report = await _seed_report(db_session, auth_service, "rep-org-uniq")
    db_session.add(
        ReportExport(
            org_id=report.org_id,
            report_id=report.id,
            format=ExportFormat.PDF,
            status=ExportStatus.QUEUED,
        )
    )
    await db_session.flush()
    db_session.add(
        ReportExport(
            org_id=report.org_id,
            report_id=report.id,
            format=ExportFormat.PDF,
            status=ExportStatus.QUEUED,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()  # (report_id, format) is unique — one export per format


async def test_rls_hides_reports_from_other_org(
    auth_service: AuthService, db_session: AsyncSession
) -> None:
    report = await _seed_report(db_session, auth_service, "rep-org-b")
    assert (await db_session.execute(text("SELECT count(*) FROM reports"))).scalar_one() == 1
    await set_tenant_context(db_session, report.created_by)  # any other org_id → RLS hides it
    assert (await db_session.execute(text("SELECT count(*) FROM reports"))).scalar_one() == 0
