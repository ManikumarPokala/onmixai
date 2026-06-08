"""Reports HTTP routes — thin: validate, one service call, shape response. Creation is async
(returns a QUEUED report; the worker generates it); reads are owner-scoped (404 for a
non-owner). Failed/declined reports are returned honestly with their reason, never as success."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse

from src.identity.dependencies import get_current_user
from src.identity.schemas import AuthContext
from src.reports.dependencies import get_export_service, get_report_service
from src.reports.schemas import (
    CreateReportRequest,
    ExportResponse,
    ReportPage,
    ReportResponse,
)
from src.reports.service import ReportExportService, ReportService
from src.shared.storage import ObjectStorage, get_object_storage

router = APIRouter()


@router.post("/reports", status_code=status.HTTP_201_CREATED)
async def create_report(
    body: CreateReportRequest,
    actor: AuthContext = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
) -> ReportResponse:
    return await service.create(
        actor,
        report_type=body.report_type,
        title=body.title,
        query=body.query,
        collection_scope=body.collection_scope,
    )


@router.get("/reports")
async def list_reports(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    actor: AuthContext = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
) -> ReportPage:
    return await service.list(actor, cursor=cursor, limit=limit)


@router.get("/reports/{report_id}")
async def get_report(
    report_id: UUID,
    actor: AuthContext = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
) -> ReportResponse:
    return await service.get(actor, report_id)


@router.post("/reports/{report_id}/exports", status_code=status.HTTP_202_ACCEPTED)
async def create_export(
    report_id: UUID,
    actor: AuthContext = Depends(get_current_user),
    service: ReportExportService = Depends(get_export_service),
) -> ExportResponse:
    return await service.create(actor, report_id)


@router.get("/reports/{report_id}/exports/{export_id}")
async def get_export(
    report_id: UUID,
    export_id: UUID,
    actor: AuthContext = Depends(get_current_user),
    service: ReportExportService = Depends(get_export_service),
) -> ExportResponse:
    return await service.get(actor, report_id, export_id)


@router.get("/reports/{report_id}/exports/{export_id}/download")
async def download_export(
    report_id: UUID,
    export_id: UUID,
    actor: AuthContext = Depends(get_current_user),
    service: ReportExportService = Depends(get_export_service),
    storage: ObjectStorage = Depends(get_object_storage),
) -> StreamingResponse:
    # ACL-checked: a non-owner / cross-org / not-ready export is a 404 here — never another
    # tenant's object. The PDF is streamed (proxied) from storage; storage keys never leak.
    key = await service.resolve_download(actor, report_id, export_id)
    return StreamingResponse(
        storage.get_stream(key),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="report-{report_id}.pdf"'},
    )
