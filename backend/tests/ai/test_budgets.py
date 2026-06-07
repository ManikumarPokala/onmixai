"""Budget enforcement: the hard cap blocks BEFORE any provider call (no spend), the
soft threshold warns + audits exactly once per period, and concurrent completions keep
the period total exact (the UPSERT increment is atomic)."""

import asyncio
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.ai.gateway import BudgetExceededError, GatewayContext
from src.ai.metering import MeteringGateway
from src.ai.models import (
    BudgetPeriod,
    TokenBudget,
    TokenUsageEvent,
    TokenUsagePeriod,
    UsageFeature,
)
from src.ai.repository import TokenBudgetRepository, TokenUsageRepository
from src.identity.service import AuthService
from src.shared.audit import AuditEmitter
from src.shared.database import set_tenant_context
from tests.ai.test_metering import _PERIOD, _clock, _ctx, _gateway, _org, _prompt
from tests.fakes.fake_gateway import FakeGateway


class _RecordingAudit(AuditEmitter):
    def __init__(self) -> None:
        self.actions: list[str] = []

    def emit(
        self,
        *,
        org_id: UUID,
        actor_id: UUID,
        action: str,
        resource_id: UUID | None = None,
        **fields: object,
    ) -> None:
        self.actions.append(action)


async def test_hard_cap_blocks_before_provider_call(
    auth_service: AuthService, db_session: AsyncSession
) -> None:
    org_id, user_id = await _org(auth_service)
    await set_tenant_context(db_session, org_id)
    db_session.add(TokenBudget(org_id=org_id, period=BudgetPeriod.MONTHLY, limit_tokens=100))
    db_session.add(TokenUsagePeriod(org_id=org_id, period_start=_PERIOD, total_tokens=100))
    await db_session.flush()
    fake = FakeGateway()
    gateway = _gateway(db_session, fake)
    with pytest.raises(BudgetExceededError):
        await gateway.complete(prompt=_prompt(), ctx=_ctx(org_id, user_id))
    assert len(fake.calls) == 0  # blocked before any provider call → zero spend


async def test_soft_threshold_warns_once_per_period(
    auth_service: AuthService, db_session: AsyncSession
) -> None:
    org_id, user_id = await _org(auth_service)
    await set_tenant_context(db_session, org_id)
    db_session.add(
        TokenBudget(
            org_id=org_id, period=BudgetPeriod.MONTHLY, limit_tokens=1000, soft_threshold_pct=80
        )
    )
    await db_session.flush()
    audit = _RecordingAudit()
    fake = FakeGateway()
    for _ in range(5):
        fake.queue_completion(prompt_tokens=200, completion_tokens=0)  # crosses 800 on the 4th
    gateway = _gateway(db_session, fake, audit=audit)
    for _ in range(5):
        await gateway.complete(prompt=_prompt(), ctx=_ctx(org_id, user_id))

    soft = [a for a in audit.actions if a == "budget.soft_threshold_crossed"]
    assert len(soft) == 1  # exactly once, though calls 4 and 5 are both over the threshold
    flag = (
        await db_session.execute(
            select(TokenUsagePeriod.soft_threshold_crossed).where(
                TokenUsagePeriod.period_start == _PERIOD
            )
        )
    ).scalar_one()
    assert flag is True


async def test_concurrent_completions_keep_period_total_exact(
    auth_service: AuthService, db_session: AsyncSession, app_engine: AsyncEngine
) -> None:
    org_id, user_id = await _org(auth_service)
    await db_session.commit()  # make the org visible to the concurrent connections
    maker = async_sessionmaker(app_engine, expire_on_commit=False)

    async def _one() -> None:
        async with maker() as session:
            await set_tenant_context(session, org_id)
            fake = FakeGateway()
            fake.queue_completion(prompt_tokens=10, completion_tokens=0)
            gateway = MeteringGateway(
                inner=fake,
                budgets=TokenBudgetRepository(session),
                usage=TokenUsageRepository(session),
                audit=AuditEmitter(),
                clock=_clock,
            )
            await gateway.complete(
                prompt=_prompt(),
                ctx=GatewayContext(org_id, user_id, UsageFeature.CHAT, "req"),
            )
            await session.commit()

    try:
        await asyncio.gather(*[_one() for _ in range(10)])
        async with maker() as session:
            await set_tenant_context(session, org_id)
            total = (
                await session.execute(
                    select(TokenUsagePeriod.total_tokens).where(
                        TokenUsagePeriod.period_start == _PERIOD
                    )
                )
            ).scalar_one()
            count = (await session.execute(select(func.count(TokenUsageEvent.id)))).scalar_one()
        assert total == 100 and count == 10  # 10 × 10, exact under concurrent UPSERTs
    finally:
        async with maker() as session:
            await session.execute(
                text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(org_id)}
            )
            await session.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": org_id})
            await session.commit()
