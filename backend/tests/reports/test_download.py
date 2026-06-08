"""Export create + download. Create is idempotent (one export per report+format); download is
an isolation surface — owner streams the PDF, a non-owner (same org) and a cross-org user get
404, and a not-ready export is 404. Never another tenant's bytes."""

from uuid import UUID, uuid4

from src.identity.models import Role, User
from src.reports.models import (
    ExportFormat,
    ExportStatus,
    Report,
    ReportExport,
    ReportStatus,
    ReportType,
)
from src.shared.database import set_tenant_context
from src.shared.security import create_access_token, hash_password
from tests.reports.conftest import ReportHarness, auth_header, register_and_login


async def _seed_ready_export(
    harness: ReportHarness, token: str, *, status: ExportStatus = ExportStatus.READY
) -> tuple[UUID, UUID, UUID]:
    """A READY report + an export (default READY, with a PDF in the fake storage). Returns
    (org_id, report_id, export_id)."""
    me = (await harness.client.get("/api/v1/users/me", headers=auth_header(token))).json()
    org_id, user_id = UUID(me["org_id"]), UUID(me["id"])
    report_id, export_id = uuid4(), uuid4()
    key = f"org/{org_id}/report/{report_id}/{export_id}.pdf"
    await set_tenant_context(harness.db_session, org_id)
    harness.db_session.add(
        Report(
            id=report_id,
            org_id=org_id,
            created_by=user_id,
            report_type=ReportType.EXECUTIVE_SUMMARY,
            title="Q3",
            source_query="q",
            status=ReportStatus.READY,
            content={"sections": [], "citations": []},
            generation_metadata={"model": "stub"},
        )
    )
    harness.db_session.add(
        ReportExport(
            id=export_id,
            org_id=org_id,
            report_id=report_id,
            format=ExportFormat.PDF,
            status=status,
            storage_key=key if status == ExportStatus.READY else None,
        )
    )
    await harness.db_session.flush()
    if status == ExportStatus.READY:
        harness.storage.objects[key] = b"%PDF-1.7 fake report bytes"
    return org_id, report_id, export_id


async def _second_user_token(harness: ReportHarness, owner_token: str) -> str:
    me = (await harness.client.get("/api/v1/users/me", headers=auth_header(owner_token))).json()
    org_id = UUID(me["org_id"])
    user_id = uuid4()
    await set_tenant_context(harness.db_session, org_id)
    harness.db_session.add(
        User(
            id=user_id,
            org_id=org_id,
            email=f"second-{user_id.hex[:8]}@a.test",
            password_hash=hash_password("password-123456"),
            full_name="Second",
            role=Role.MEMBER,
        )
    )
    await harness.db_session.flush()
    return create_access_token(
        settings=harness.settings, user_id=user_id, org_id=org_id, role=Role.MEMBER.value
    )


async def test_create_export_is_idempotent(report_harness: ReportHarness) -> None:
    token = await register_and_login(report_harness.client, "acme")
    _org, report_id, _export = await _seed_ready_export(report_harness, token)
    # First create: an export already exists for (report, pdf) → returns it (idempotent).
    first = await report_harness.client.post(
        f"/api/v1/reports/{report_id}/exports", headers=auth_header(token)
    )
    assert first.status_code == 202
    second = await report_harness.client.post(
        f"/api/v1/reports/{report_id}/exports", headers=auth_header(token)
    )
    assert second.json()["id"] == first.json()["id"]  # not duplicated


async def test_owner_downloads_the_pdf(report_harness: ReportHarness) -> None:
    token = await register_and_login(report_harness.client, "acme")
    _org, report_id, export_id = await _seed_ready_export(report_harness, token)
    resp = await report_harness.client.get(
        f"/api/v1/reports/{report_id}/exports/{export_id}/download", headers=auth_header(token)
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


async def test_non_owner_same_org_download_is_404(report_harness: ReportHarness) -> None:
    owner = await register_and_login(report_harness.client, "acme")
    _org, report_id, export_id = await _seed_ready_export(report_harness, owner)
    other = await _second_user_token(report_harness, owner)
    resp = await report_harness.client.get(
        f"/api/v1/reports/{report_id}/exports/{export_id}/download", headers=auth_header(other)
    )
    assert resp.status_code == 404  # never another user's bytes


async def test_cross_org_download_is_404(report_harness: ReportHarness) -> None:
    a_token = await register_and_login(report_harness.client, "orga")
    _org, report_id, export_id = await _seed_ready_export(report_harness, a_token)
    b_token = await register_and_login(report_harness.client, "orgb")
    resp = await report_harness.client.get(
        f"/api/v1/reports/{report_id}/exports/{export_id}/download", headers=auth_header(b_token)
    )
    assert resp.status_code == 404  # never another tenant's bytes


async def test_not_ready_export_download_is_404(report_harness: ReportHarness) -> None:
    token = await register_and_login(report_harness.client, "acme")
    _org, report_id, export_id = await _seed_ready_export(
        report_harness, token, status=ExportStatus.QUEUED
    )
    resp = await report_harness.client.get(
        f"/api/v1/reports/{report_id}/exports/{export_id}/download", headers=auth_header(token)
    )
    assert resp.status_code == 404
