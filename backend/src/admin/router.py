"""Admin HTTP surface. Thin: validate query → one service call → shape response (CLAUDE.md
§3.1). Every route is admin-gated (require_admin); the audit query is org-scoped and read-only,
and viewing it is itself audited (admin.audit_viewed) in the service."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from src.admin.dependencies import require_admin
from src.ai.config_schemas import (
    BudgetResponse,
    ModelConfigResponse,
    SetBudgetRequest,
    SetModelConfigRequest,
)
from src.ai.config_service import AIConfigService
from src.ai.dependencies import get_ai_config_service
from src.conversation.curation_service import FeedbackCurationService
from src.conversation.dependencies import get_feedback_curation_service
from src.conversation.models import GoldenCandidateStatus
from src.conversation.schemas import (
    DecisionRequest,
    GoldenCandidatePage,
    GoldenCandidateResponse,
    ReviewPage,
)
from src.governance.analytics import AnalyticsService
from src.governance.dependencies import (
    get_analytics_service,
    get_audit_query_service,
    get_retention_policy_service,
)
from src.governance.schemas import (
    AuditEventPage,
    AuditFilter,
    RetentionPolicyResponse,
    SetRetentionPolicyRequest,
    UsageAnalytics,
)
from src.governance.service import AuditQueryService, RetentionPolicyService
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
from src.knowledge.admin_service import KnowledgeAdminService
from src.knowledge.dependencies import get_knowledge_admin_service
from src.knowledge.schemas import DocumentPage, QuotaUsage

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
    """Update the org profile and document quota (audited)."""
    return await service.update_organization(
        actor, name=body.name, max_documents=body.max_documents
    )


@router.get("/knowledge/quota", response_model=QuotaUsage)
async def knowledge_quota(
    actor: AuthContext = Depends(require_admin),
    service: KnowledgeAdminService = Depends(get_knowledge_admin_service),
) -> QuotaUsage:
    """The org's document quota usage — used / limit / remaining (owner/admin)."""
    return await service.quota_usage(actor)


@router.get("/knowledge/documents", response_model=DocumentPage)
async def list_org_documents(
    actor: AuthContext = Depends(require_admin),
    service: KnowledgeAdminService = Depends(get_knowledge_admin_service),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1),
) -> DocumentPage:
    """One newest-first page of every document in the org, across all collections (owner/admin)."""
    return await service.list_documents(actor, cursor=cursor, limit=limit)


@router.post("/knowledge/documents/{document_id}/reindex", status_code=202)
async def reindex_document(
    document_id: UUID,
    actor: AuthContext = Depends(require_admin),
    service: KnowledgeAdminService = Depends(get_knowledge_admin_service),
) -> None:
    """Force-requeue a document for an idempotent chunk/embedding rebuild (audited)."""
    await service.reindex_document(actor, document_id)


@router.delete("/knowledge/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: UUID,
    actor: AuthContext = Depends(require_admin),
    service: KnowledgeAdminService = Depends(get_knowledge_admin_service),
) -> None:
    """Delete any document in the org and compensate its storage object (audited)."""
    await service.delete_document(actor, document_id)


@router.get("/retention-policy", response_model=RetentionPolicyResponse)
async def get_retention_policy(
    actor: AuthContext = Depends(require_admin),
    service: RetentionPolicyService = Depends(get_retention_policy_service),
) -> RetentionPolicyResponse:
    """The org's data-retention policy, or retain-by-default when unset (owner/admin)."""
    return await service.get_policy(actor)


@router.put("/retention-policy", response_model=RetentionPolicyResponse)
async def set_retention_policy(
    body: SetRetentionPolicyRequest,
    actor: AuthContext = Depends(require_admin),
    service: RetentionPolicyService = Depends(get_retention_policy_service),
) -> RetentionPolicyResponse:
    """Set the org's retention windows (audited). Null/omitted means retain-by-default."""
    return await service.set_policy(actor, body)


@router.get("/ai/model-config", response_model=ModelConfigResponse)
async def get_model_config(
    actor: AuthContext = Depends(require_admin),
    service: AIConfigService = Depends(get_ai_config_service),
) -> ModelConfigResponse:
    """The org's LLM routing config — its row, or platform defaults when unset (owner/admin)."""
    return await service.get_model_config(actor.org_id)


@router.put("/ai/model-config", response_model=ModelConfigResponse)
async def set_model_config(
    body: SetModelConfigRequest,
    actor: AuthContext = Depends(require_admin),
    service: AIConfigService = Depends(get_ai_config_service),
) -> ModelConfigResponse:
    """Replace the org's model config (audited). 422 on a bad model ref or empty fallback chain."""
    return await service.set_model_config(org_id=actor.org_id, actor_id=actor.user_id, body=body)


@router.put("/ai/budget", response_model=BudgetResponse)
async def set_budget(
    body: SetBudgetRequest,
    actor: AuthContext = Depends(require_admin),
    service: AIConfigService = Depends(get_ai_config_service),
) -> BudgetResponse:
    """Set the org's monthly token budget (audited). Effective on the next metered call."""
    return await service.set_budget(org_id=actor.org_id, actor_id=actor.user_id, body=body)


@router.get("/feedback/review", response_model=ReviewPage)
async def review_feedback(
    actor: AuthContext = Depends(require_admin),
    service: FeedbackCurationService = Depends(get_feedback_curation_service),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1),
) -> ReviewPage:
    """One page of UP-rated answers, surfaced PII-redacted for golden curation (owner/admin)."""
    return await service.list_review_queue(actor, cursor=cursor, limit=limit)


@router.post("/feedback/{message_id}/promote", response_model=GoldenCandidateResponse)
async def promote_to_golden_candidate(
    message_id: UUID,
    actor: AuthContext = Depends(require_admin),
    service: FeedbackCurationService = Depends(get_feedback_curation_service),
) -> GoldenCandidateResponse:
    """Promote an answer's Q&A into a PENDING golden candidate, stored PII-redacted (audited)."""
    return await service.promote(actor, message_id)


@router.get("/golden-candidates", response_model=GoldenCandidatePage)
async def list_golden_candidates(
    actor: AuthContext = Depends(require_admin),
    service: FeedbackCurationService = Depends(get_feedback_curation_service),
    status: GoldenCandidateStatus | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1),
) -> GoldenCandidatePage:
    """One page of golden candidates, optionally filtered by status (owner/admin)."""
    return await service.list_candidates(actor, status=status, cursor=cursor, limit=limit)


@router.post("/golden-candidates/{candidate_id}/decision", response_model=GoldenCandidateResponse)
async def decide_golden_candidate(
    candidate_id: UUID,
    body: DecisionRequest,
    actor: AuthContext = Depends(require_admin),
    service: FeedbackCurationService = Depends(get_feedback_curation_service),
) -> GoldenCandidateResponse:
    """Human gate: approve or reject a candidate (audited). Never writes the eval golden set."""
    return await service.decide(actor, candidate_id, approve=body.decision == "approve")
