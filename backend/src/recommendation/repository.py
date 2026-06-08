"""Recommendation domain queries. Tenant-scoped (RLS + org_id predicate); the per-user
ownership rule is applied in the service over what these return. No business decisions here
(CLAUDE.md §3.1). Lists are bounded by a caller-supplied limit (no unbounded SELECT)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from src.recommendation.models import Recommendation


class RecommendationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, recommendation: Recommendation) -> Recommendation:
        """Persist a recommendation (completed or declined). Time: O(1)."""
        self._session.add(recommendation)
        await self._session.flush()
        return recommendation

    async def get(self, org_id: UUID, recommendation_id: UUID) -> Recommendation | None:
        result = await self._session.execute(
            select(Recommendation).where(
                Recommendation.org_id == org_id, Recommendation.id == recommendation_id
            )
        )
        return result.scalar_one_or_none()

    async def list_for_owner(
        self,
        org_id: UUID,
        created_by: UUID,
        *,
        limit: int,
        before: tuple[datetime, UUID] | None,
    ) -> list[Recommendation]:
        """One page of the owner's recommendations, newest first, via a (created_at, id)
        keyset cursor. Time: O(limit) on ix_recommendations_org_creator_created."""
        stmt = select(Recommendation).where(
            Recommendation.org_id == org_id, Recommendation.created_by == created_by
        )
        if before is not None:
            stmt = stmt.where(
                tuple_(Recommendation.created_at, Recommendation.id)
                < tuple_(literal(before[0]), literal(before[1]))
            )
        stmt = stmt.order_by(Recommendation.created_at.desc(), Recommendation.id.desc()).limit(
            limit
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
