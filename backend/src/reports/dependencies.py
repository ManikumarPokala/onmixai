"""Reports FastAPI dependencies — compose ReportService from its repository, the job queue,
and audit. Generation runs in the worker; the API only creates + reads. Constructor injection
only."""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.reports.repository import ReportRepository
from src.reports.service import ReportService
from src.shared.audit import AuditEmitter, get_audit_emitter
from src.shared.config import Settings, get_settings
from src.shared.database import get_db_session
from src.shared.queue import JobQueue, get_job_queue


def get_report_service(
    session: AsyncSession = Depends(get_db_session),
    audit: AuditEmitter = Depends(get_audit_emitter),
    settings: Settings = Depends(get_settings),
    queue: JobQueue = Depends(get_job_queue),
) -> ReportService:
    return ReportService(
        session=session,
        repository=ReportRepository(session),
        queue=queue,
        audit=audit,
        settings=settings,
    )
