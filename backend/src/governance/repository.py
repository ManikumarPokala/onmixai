"""Governance read queries. The audit query is keyset-paginated over the (shared) audit store;
the analytics aggregates are a cross-cutting read model over existing tables via raw SQL — so
governance does not import another domain's ORM models (CLAUDE.md §3.3) — and are index-backed,
never full scans (plan-asserted). No business decisions here."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import literal, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from src.governance.models import RetentionPolicy
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


class AnalyticsRepository:
    """Org-scoped usage aggregates as a read model (raw SQL — no cross-domain model imports).
    Every query is index-backed (plan-asserted): token_usage_events (org+feature+created_at /
    org+created_at), audit_events (org+action+created_at / org+created_at), documents
    (org+collection+status). RLS scopes each table to the actor's org; the org_id predicate is
    defense in depth."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def tokens_by_feature(
        self, org_id: UUID, start: datetime, end: datetime
    ) -> dict[str, int]:
        """Sum of tokens per feature in [start, end)."""
        rows = await self._session.execute(
            text(
                "SELECT feature, COALESCE(SUM(total_tokens), 0) AS total "
                "FROM token_usage_events "
                "WHERE org_id = :org AND created_at >= :start AND created_at < :end "
                "GROUP BY feature"
            ),
            {"org": org_id, "start": start, "end": end},
        )
        return {str(feature): int(total) for feature, total in rows.all()}

    async def document_stats(self, org_id: UUID) -> tuple[int, int]:
        """(count, total storage bytes) of the org's live (non-superseded) documents."""
        count, storage = (
            await self._session.execute(
                text(
                    "SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM documents "
                    "WHERE org_id = :org AND superseded = false"
                ),
                {"org": org_id},
            )
        ).one()
        return int(count), int(storage)

    async def action_count(self, org_id: UUID, action: str, start: datetime, end: datetime) -> int:
        """Count of an audit action in [start, end)."""
        return int(
            (
                await self._session.execute(
                    text(
                        "SELECT COUNT(*) FROM audit_events "
                        "WHERE org_id = :org AND action = :action "
                        "AND created_at >= :start AND created_at < :end"
                    ),
                    {"org": org_id, "action": action, "start": start, "end": end},
                )
            ).scalar_one()
        )

    async def active_user_count(self, org_id: UUID, start: datetime, end: datetime) -> int:
        """Distinct actors who did anything in [start, end)."""
        return int(
            (
                await self._session.execute(
                    text(
                        "SELECT COUNT(DISTINCT actor_user_id) FROM audit_events "
                        "WHERE org_id = :org AND created_at >= :start AND created_at < :end"
                    ),
                    {"org": org_id, "start": start, "end": end},
                )
            ).scalar_one()
        )


class RetentionPolicyRepository:
    """The org's single data-retention policy row (read + upsert). Tenant-scoped by RLS."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, org_id: UUID) -> RetentionPolicy | None:
        """The org's retention policy, or None when unset (retain-by-default). Time: O(1)."""
        result = await self._session.execute(
            select(RetentionPolicy).where(RetentionPolicy.org_id == org_id)
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        org_id: UUID,
        *,
        audit_retention_days: int | None,
        conversation_retention_days: int | None,
        updated_by: UUID,
    ) -> RetentionPolicy:
        """Create or update the org's retention policy (one row per org). Time: O(1)."""
        policy = await self.get(org_id)
        if policy is None:
            policy = RetentionPolicy(org_id=org_id)
            self._session.add(policy)
        policy.audit_retention_days = audit_retention_days
        policy.conversation_retention_days = conversation_retention_days
        policy.updated_by = updated_by
        await self._session.flush()
        return policy
