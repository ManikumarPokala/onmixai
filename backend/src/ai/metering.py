"""Metering + budget enforcement — wraps an ``LLMGateway`` so token counting and
budgets live in exactly one place (CLAUDE.md §6). It decorates the adapter (or the
fake), so the same metering code runs in tests.

Budget semantics (ADR 0012): the hard cap is checked BEFORE the provider call from the
materialized period total, so a blocked request never spends. The check is approximate
(it does not know this call's tokens); a call already admitted finishes and is recorded
EXACTLY post-call — so a single request may push slightly over, and the *next* request
is blocked. No mid-stream truncation. Failed calls (the inner gateway raised) meter
nothing — only a successful completion's tokens count.
"""

from collections.abc import Callable
from datetime import UTC, datetime

import structlog
from pydantic import BaseModel

from src.ai.gateway import (
    BudgetExceededError,
    Completion,
    GatewayContext,
    LLMGateway,
    ModelRef,
    RenderedPrompt,
)
from src.ai.models import BudgetPeriod
from src.ai.repository import TokenBudgetRepository, TokenUsageRepository
from src.ai.rules import crossed_soft_threshold, monthly_period_start
from src.shared.audit import AuditEmitter

_logger = structlog.get_logger()


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MeteringGateway:
    """An ``LLMGateway`` decorator: pre-call hard-cap enforcement + exact post-call
    metering, in the same transaction as the request's unit of work."""

    def __init__(
        self,
        *,
        inner: LLMGateway,
        budgets: TokenBudgetRepository,
        usage: TokenUsageRepository,
        audit: AuditEmitter,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._inner = inner
        self._budgets = budgets
        self._usage = usage
        self._audit = audit
        self._clock = clock

    async def complete(
        self,
        *,
        prompt: RenderedPrompt,
        ctx: GatewayContext,
        model: ModelRef | None = None,
        response_schema: type[BaseModel] | None = None,
    ) -> Completion:
        """Enforce budget → delegate → record. Raises ``BudgetExceededError`` (429)
        before any spend when over the hard cap. Time: O(1) budget check + the inner
        call + O(1) record."""
        period_start = monthly_period_start(self._clock())
        budget = await self._budgets.get(ctx.org_id, BudgetPeriod.MONTHLY)

        if budget is not None:
            current = await self._usage.period_total(ctx.org_id, period_start)
            if current >= budget.limit_tokens:
                raise BudgetExceededError(
                    detail=f"org {ctx.org_id} period total {current} >= limit {budget.limit_tokens}"
                )

        completion = await self._inner.complete(
            prompt=prompt, ctx=ctx, model=model, response_schema=response_schema
        )

        await self._usage.add_event(
            org_id=ctx.org_id,
            user_id=ctx.user_id,
            feature=ctx.feature,
            model=completion.model_used,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            total_tokens=completion.total_tokens,
            trace_id=completion.trace_id,
            request_id=ctx.request_id,
        )
        new_total = await self._usage.increment_period(
            ctx.org_id, period_start, completion.total_tokens
        )

        if budget is not None and crossed_soft_threshold(
            new_total, budget.limit_tokens, budget.soft_threshold_pct
        ):
            # Compare-and-set so the warn + audit fire exactly once per period.
            if await self._usage.mark_soft_crossed_if_unset(ctx.org_id, period_start):
                self._audit.emit(
                    org_id=ctx.org_id,
                    actor_id=ctx.user_id,
                    action="budget.soft_threshold_crossed",
                    period_total=new_total,
                    limit_tokens=budget.limit_tokens,
                )
                _logger.warning(
                    "budget.soft_threshold_crossed",
                    org_id=str(ctx.org_id),
                    period_total=new_total,
                    limit_tokens=budget.limit_tokens,
                )
        return completion
