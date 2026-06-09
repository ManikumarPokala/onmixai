"""Admin HTTP surface. Thin: validate query → one service call → shape response (CLAUDE.md
§3.1). Every route is admin-gated (require_admin); the audit query is org-scoped and read-only,
and viewing it is itself audited (admin.audit_viewed) in the service."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from src.admin.dependencies import require_admin
from src.governance.dependencies import get_audit_query_service
from src.governance.schemas import AuditEventPage, AuditFilter
from src.governance.service import AuditQueryService
from src.identity.schemas import AuthContext

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/audit", response_model=AuditEventPage)
async def list_audit_events(
    actor: AuthContext = Depends(require_admin),
    service: AuditQueryService = Depends(get_audit_query_service),
    actor_user_id: UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    resource_id: UUID | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1),
) -> AuditEventPage:
    """One org-scoped, filtered, newest-first page of the audit log (owner/admin only)."""
    filters = AuditFilter(
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        start=start,
        end=end,
    )
    return await service.list_events(actor, filters=filters, cursor=cursor, limit=limit)
