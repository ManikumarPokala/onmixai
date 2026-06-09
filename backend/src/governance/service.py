"""Governance services. The audit query is the read side of the immutable store; reading the
audit log is itself a sensitive action, so an admin viewing it is audited (admin.audit_viewed,
the meta-rule). Org-scoped (RLS + the org_id predicate); read-only — no audit mutation exists."""

from src.governance.repository import AuditEventQueryRepository
from src.governance.schemas import AuditEventPage, AuditEventResponse, AuditFilter
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
