"""Admin HTTP surface. Thin: validate query → one service call → shape response (CLAUDE.md
§3.1). Every route is admin-gated (require_admin); the audit query is org-scoped and read-only,
and viewing it is itself audited (admin.audit_viewed) in the service."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from src.admin.dependencies import require_admin
from src.governance.analytics import AnalyticsService
from src.governance.dependencies import get_analytics_service, get_audit_query_service
from src.governance.schemas import AuditEventPage, AuditFilter, UsageAnalytics
from src.governance.service import AuditQueryService
from src.identity.dependencies import get_user_admin_service
from src.identity.schemas import (
    AuthContext,
    ChangeRoleRequest,
    OrganizationResponse,
    UpdateOrganizationRequest,
    UserPage,
    UserResponse,
)
from src.identity.service import UserAdminService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/analytics/usage", response_model=UsageAnalytics)
async def usage_analytics(
    actor: AuthContext = Depends(require_admin),
    service: AnalyticsService = Depends(get_analytics_service),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> UsageAnalytics:
    """Org-scoped usage over [start, end) (defaults to the last 30 days); owner/admin only."""
    return await service.usage(actor, start=start, end=end)


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


@router.get("/users", response_model=UserPage)
async def list_users(
    actor: AuthContext = Depends(require_admin),
    service: UserAdminService = Depends(get_user_admin_service),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1),
) -> UserPage:
    """One newest-first page of the org's users (owner/admin only)."""
    return await service.list_users(actor, cursor=cursor, limit=limit)


@router.post("/users/{user_id}/deactivate", response_model=UserResponse)
async def deactivate_user(
    user_id: UUID,
    actor: AuthContext = Depends(require_admin),
    service: UserAdminService = Depends(get_user_admin_service),
) -> UserResponse:
    """Deactivate a user — revokes their sessions immediately (audited)."""
    return await service.set_active(actor, user_id, active=False)


@router.post("/users/{user_id}/activate", response_model=UserResponse)
async def activate_user(
    user_id: UUID,
    actor: AuthContext = Depends(require_admin),
    service: UserAdminService = Depends(get_user_admin_service),
) -> UserResponse:
    """Reactivate a user (audited)."""
    return await service.set_active(actor, user_id, active=True)


@router.patch("/users/{user_id}/role", response_model=UserResponse)
async def change_user_role(
    user_id: UUID,
    body: ChangeRoleRequest,
    actor: AuthContext = Depends(require_admin),
    service: UserAdminService = Depends(get_user_admin_service),
) -> UserResponse:
    """Change a user's role under the owner rules (only owners grant/alter owner; audited)."""
    return await service.change_role(actor, user_id, body)


@router.get("/organization", response_model=OrganizationResponse)
async def get_organization(
    actor: AuthContext = Depends(require_admin),
    service: UserAdminService = Depends(get_user_admin_service),
) -> OrganizationResponse:
    """The actor's organization profile (owner/admin only)."""
    return await service.get_organization(actor)


@router.patch("/organization", response_model=OrganizationResponse)
async def update_organization(
    body: UpdateOrganizationRequest,
    actor: AuthContext = Depends(require_admin),
    service: UserAdminService = Depends(get_user_admin_service),
) -> OrganizationResponse:
    """Update the org profile (audited)."""
    return await service.update_organization(actor, name=body.name)
