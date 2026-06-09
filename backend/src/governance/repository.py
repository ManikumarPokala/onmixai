"""Governance read queries over the (shared) audit store. Org-scoped + filtered + keyset
paginated; the (org_id, created_at) / (org_id, action, created_at) / (org_id, resource_type,
resource_id) indexes (migration 0009) serve every predicate. No business decisions here."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from src.governance.schemas import AuditFilter
from src.shared.audit import AuditEvent


class AuditEventQueryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_org(
        self,
        org_id: UUID,
        filters: AuditFilter,
        *,
        limit: int,
        before: tuple[datetime, UUID] | None,
    ) -> list[AuditEvent]:
        """One newest-first page of an org's audit events matching ``filters``, via a
        (created_at, id) keyset cursor. Time: O(limit) on the org/action/resource indexes."""
        stmt = select(AuditEvent).where(AuditEvent.org_id == org_id)
        if filters.actor_user_id is not None:
            stmt = stmt.where(AuditEvent.actor_user_id == filters.actor_user_id)
        if filters.action is not None:
            stmt = stmt.where(AuditEvent.action == filters.action)
        if filters.resource_type is not None:
            stmt = stmt.where(AuditEvent.resource_type == filters.resource_type)
        if filters.resource_id is not None:
            stmt = stmt.where(AuditEvent.resource_id == filters.resource_id)
        if filters.start is not None:
            stmt = stmt.where(AuditEvent.created_at >= filters.start)
        if filters.end is not None:
            stmt = stmt.where(AuditEvent.created_at < filters.end)
        if before is not None:
            stmt = stmt.where(
                tuple_(AuditEvent.created_at, AuditEvent.id)
                < tuple_(literal(before[0]), literal(before[1]))
            )
        stmt = stmt.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
