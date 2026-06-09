"""Governance DI wiring (constructor injection; CLAUDE.md §3.1)."""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.governance.repository import AuditEventQueryRepository
from src.governance.service import AuditQueryService
from src.shared.audit import AuditEmitter, get_audit_emitter
from src.shared.config import Settings, get_settings
from src.shared.database import get_db_session


def get_audit_query_service(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    audit: AuditEmitter = Depends(get_audit_emitter),
) -> AuditQueryService:
    return AuditQueryService(
        repository=AuditEventQueryRepository(session), audit=audit, settings=settings
    )
