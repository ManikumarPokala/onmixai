"""Governance services. The audit query is the read side of the immutable store; reading the
audit log is itself a sensitive action, so an admin viewing it is audited (admin.audit_viewed,
the meta-rule). Org-scoped (RLS + the org_id predicate); read-only — no audit mutation exists."""

from src.governance.repository import AuditEventQueryRepository, RetentionPolicyRepository
from src.governance.schemas import (
    AuditEventPage,
    AuditEventResponse,
    AuditFilter,
    RetentionPolicyResponse,
    SetRetentionPolicyRequest,
)
from src.identity.schemas import AuthContext
from src.shared.audit import AuditEmitter
from src.shared.config import Settings
from src.shared.pagination import decode_keyset_cursor, encode_keyset_cursor


class AuditQueryService:
    def __init__(
        self,
        *,
        repository: AuditEventQueryRepository,
        audit: AuditEmitter,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._audit = audit
        self._settings = settings

    async def list_events(
        self, actor: AuthContext, *, filters: AuditFilter, cursor: str | None, limit: int
    ) -> AuditEventPage:
        """One newest-first page of the actor's org audit events (filtered, keyset-paginated,
        server-capped). Emits admin.audit_viewed. Time: O(limit). Raises INVALID_CURSOR."""
        capped = min(limit, self._settings.admin_audit_page_size)
        before = decode_keyset_cursor(cursor) if cursor is not None else None
        rows = await self._repository.list_for_org(
            actor.org_id, filters, limit=capped + 1, before=before
        )
        has_more = len(rows) > capped
        page = rows[:capped]
        next_cursor = encode_keyset_cursor(page[-1].created_at, page[-1].id) if has_more else None
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="admin.audit_viewed",
            result_count=len(page),
        )
        return AuditEventPage(
            events=[AuditEventResponse.from_model(r) for r in page], next_cursor=next_cursor
        )


class RetentionPolicyService:
    """Read/update the org's data-retention policy (owner/admin). Every change is audited. The
    policy is declarative only — the destructive purge job (Task 7) reads it; null/zero windows
    mean retain-by-default, so an unset policy never deletes anything."""

    def __init__(self, *, repository: RetentionPolicyRepository, audit: AuditEmitter) -> None:
        self._repository = repository
        self._audit = audit

    async def get_policy(self, actor: AuthContext) -> RetentionPolicyResponse:
        """The org's retention policy, or retain-by-default when none is set. Time: O(1)."""
        policy = await self._repository.get(actor.org_id)
        return (
            RetentionPolicyResponse.from_model(policy)
            if policy is not None
            else RetentionPolicyResponse.retain_by_default()
        )

    async def set_policy(
        self, actor: AuthContext, body: SetRetentionPolicyRequest
    ) -> RetentionPolicyResponse:
        """Set the org's retention windows (audited). Time: O(1)."""
        policy = await self._repository.upsert(
            actor.org_id,
            audit_retention_days=body.audit_retention_days,
            conversation_retention_days=body.conversation_retention_days,
            updated_by=actor.user_id,
        )
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="retention_policy_changed",
            resource_type="retention_policy",
            resource_id=policy.id,
            audit_retention_days=policy.audit_retention_days,
            conversation_retention_days=policy.conversation_retention_days,
        )
        return RetentionPolicyResponse.from_model(policy)
