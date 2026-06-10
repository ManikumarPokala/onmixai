"""AI domain queries. All reads are tenant-scoped (the session carries the org via
RLS + the org_id predicate); no business decisions live here (CLAUDE.md §3.1)."""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.models import (
    BudgetPeriod,
    ModelConfig,
    TokenBudget,
    TokenUsageEvent,
    TokenUsagePeriod,
    UsageFeature,
)


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

    async def upsert(
        self,
        org_id: UUID,
        *,
        default_model: str,
        fallback_chain: list[str],
        temperature_default: float | None,
        pii_redaction_enabled: bool,
        updated_by: UUID,
    ) -> ModelConfig:
        """Create or update the org's model config (one row per org). Time: O(1)."""
        config = await self.get(org_id)
        if config is None:
            config = ModelConfig(org_id=org_id)
            self._session.add(config)
        config.default_model = default_model
        config.fallback_chain = fallback_chain
        config.temperature_default = temperature_default
        config.pii_redaction_enabled = pii_redaction_enabled
        config.updated_by = updated_by
        await self._session.flush()
        return config


class TokenBudgetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, org_id: UUID, period: BudgetPeriod) -> TokenBudget | None:
        """The org's budget for ``period``, or None → unlimited. Time: O(1)."""
        result = await self._session.execute(
            select(TokenBudget).where(TokenBudget.org_id == org_id, TokenBudget.period == period)
        )
        return result.scalar_one_or_none()

    async def upsert(
        self, org_id: UUID, period: BudgetPeriod, *, limit_tokens: int, soft_threshold_pct: int
    ) -> TokenBudget:
        """Create or update the org's budget for ``period`` (one row per org+period)."""
        budget = await self.get(org_id, period)
        if budget is None:
            budget = TokenBudget(org_id=org_id, period=period)
            self._session.add(budget)
        budget.limit_tokens = limit_tokens
        budget.soft_threshold_pct = soft_threshold_pct
        await self._session.flush()
        return budget


class TokenUsageRepository:
    """Append-only usage events + the O(1) materialized period total. No UPDATE of
    events ever; the period row is maintained by an atomic UPSERT-increment."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def period_total(self, org_id: UUID, period_start: datetime) -> int:
        """Current period total (0 if no row yet) — the O(1) pre-call budget read."""
        result = await self._session.execute(
            select(TokenUsagePeriod.total_tokens).where(
                TokenUsagePeriod.org_id == org_id,
                TokenUsagePeriod.period_start == period_start,
            )
        )
        return result.scalar_one_or_none() or 0

    async def add_event(
        self,
        *,
        org_id: UUID,
        user_id: UUID | None,
        feature: UsageFeature,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        trace_id: str,
        request_id: str,
    ) -> None:
        """Append one immutable usage event (the auditable source of truth)."""
        self._session.add(
            TokenUsageEvent(
                org_id=org_id,
                user_id=user_id,
                feature=feature,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                trace_id=trace_id,
                request_id=request_id,
            )
        )
        await self._session.flush()

    async def increment_period(self, org_id: UUID, period_start: datetime, delta: int) -> int:
        """Atomically add ``delta`` to the period total (insert-or-increment), returning
        the new total. Atomic at the row level, so concurrent completions stay exact.
        Time: O(1)."""
        stmt = (
            pg_insert(TokenUsagePeriod)
            .values(org_id=org_id, period_start=period_start, total_tokens=delta)
            .on_conflict_do_update(
                constraint="uq_token_usage_periods_org_id_period_start",
                set_={
                    "total_tokens": TokenUsagePeriod.total_tokens + delta,
                    "updated_at": func.now(),
                },
            )
            .returning(TokenUsagePeriod.total_tokens)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def mark_soft_crossed_if_unset(self, org_id: UUID, period_start: datetime) -> bool:
        """Compare-and-set the soft-threshold flag; returns True for the single caller
        that flips it (so the warn + audit fire once per period). Time: O(1)."""
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                update(TokenUsagePeriod)
                .where(
                    TokenUsagePeriod.org_id == org_id,
                    TokenUsagePeriod.period_start == period_start,
                    TokenUsagePeriod.soft_threshold_crossed.is_(False),
                )
                .values(soft_threshold_crossed=True)
            ),
        )
        return result.rowcount == 1
