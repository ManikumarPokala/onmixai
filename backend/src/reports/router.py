"""Reports HTTP routes — thin: validate, one service call, shape response. Creation is async
(returns a QUEUED report; the worker generates it); reads are owner-scoped (404 for a
non-owner). Failed/declined reports are returned honestly with their reason, never as success."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from src.identity.dependencies import get_current_user
from src.identity.schemas import AuthContext
from src.reports.dependencies import get_report_service
from src.reports.schemas import CreateReportRequest, ReportPage, ReportResponse
from src.reports.service import ReportService

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
