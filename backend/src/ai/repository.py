"""AI domain queries. All reads are tenant-scoped (the session carries the org via
RLS + the org_id predicate); no business decisions live here (CLAUDE.md §3.1)."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.models import ModelConfig


class ModelConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, org_id: UUID) -> ModelConfig | None:
        """The org's model config, or None → caller falls back to platform defaults.

        Time: O(1) (unique index on org_id). Space: O(1).
        """
        result = await self._session.execute(
            select(ModelConfig).where(ModelConfig.org_id == org_id)
        )
        return result.scalar_one_or_none()
