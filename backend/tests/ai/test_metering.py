"""Metering: exact post-call recording and the reconciliation invariant
(sum(events) == period total == sum(provider usage)); a failed call meters nothing;
per-feature attribution; trace_id round-trips into the usage event."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.gateway import (
    ChatMessage,
    GatewayContext,
    RenderedPrompt,
    UpstreamUnavailableError,
)
from src.ai.metering import MeteringGateway
from src.ai.models import TokenUsageEvent, TokenUsagePeriod, UsageFeature
from src.ai.repository import TokenBudgetRepository, TokenUsageRepository
from src.identity.service import AuthService
from src.shared.audit import AuditEmitter
from src.shared.database import set_tenant_context
from tests.fakes.fake_gateway import FakeGateway

_PERIOD = datetime(2026, 6, 1, tzinfo=UTC)


def _clock() -> datetime:
    return datetime(2026, 6, 15, tzinfo=UTC)  # fixed → period_start 2026-06-01


def _prompt() -> RenderedPrompt:
    return RenderedPrompt("t", "1.0.0", (ChatMessage("user", "hi"),), "h")


def _ctx(org_id: UUID, user_id: UUID, feature: UsageFeature = UsageFeature.CHAT) -> GatewayContext:
    return GatewayContext(org_id, user_id, feature, "req-1")


async def _org(auth_service: AuthService) -> tuple[UUID, UUID]:
    suffix = uuid4().hex[:8]
    result = await auth_service.register_organization(
        name="MeterOrg",
        slug=f"meter-{suffix}",
        owner_email=f"o-{suffix}@m.test",
        full_name="O",
        password="password-123456",
    )
    return result.organization.id, result.owner.id


def _gateway(
    session: AsyncSession, inner: FakeGateway, audit: AuditEmitter | None = None
) -> MeteringGateway:
    return MeteringGateway(
        inner=inner,
        budgets=TokenBudgetRepository(session),
        usage=TokenUsageRepository(session),
        audit=audit or AuditEmitter(),
        clock=_clock,
    )


async def test_metering_reconciles_events_period_and_provider_usage(
    auth_service: AuthService, db_session: AsyncSession
) -> None:
    org_id, user_id = await _org(auth_service)
    await set_tenant_context(db_session, org_id)
    fake = FakeGateway()
    for _ in range(3):
        fake.queue_completion(prompt_tokens=100, completion_tokens=50)  # 150 each → 450
    gateway = _gateway(db_session, fake)
    for _ in range(3):
        await gateway.complete(prompt=_prompt(), ctx=_ctx(org_id, user_id))

    events_sum = (
        await db_session.execute(select(func.coalesce(func.sum(TokenUsageEvent.total_tokens), 0)))
    ).scalar_one()
    period_total = (
        await db_session.execute(
            select(TokenUsagePeriod.total_tokens).where(TokenUsagePeriod.period_start == _PERIOD)
        )
    ).scalar_one()
    assert events_sum == period_total == 450  # == sum of the provider-reported usage


async def test_failed_call_meters_nothing(
    auth_service: AuthService, db_session: AsyncSession
) -> None:
    org_id, user_id = await _org(auth_service)
    await set_tenant_context(db_session, org_id)
    fake = FakeGateway()
    fake.queue_error(UpstreamUnavailableError())
    gateway = _gateway(db_session, fake)
    try:
        await gateway.complete(prompt=_prompt(), ctx=_ctx(org_id, user_id))
    except UpstreamUnavailableError:
        pass
    events = (await db_session.execute(select(func.count(TokenUsageEvent.id)))).scalar_one()
    periods = (await db_session.execute(select(func.count(TokenUsagePeriod.id)))).scalar_one()
    assert events == 0 and periods == 0  # a failed provider call is never metered


async def test_per_feature_attribution_and_trace_round_trip(
    auth_service: AuthService, db_session: AsyncSession
) -> None:
    org_id, user_id = await _org(auth_service)
    await set_tenant_context(db_session, org_id)
    fake = FakeGateway()
    fake.queue_completion(prompt_tokens=10, completion_tokens=5, trace_id="trace-chat")
    fake.queue_completion(prompt_tokens=20, completion_tokens=5, trace_id="trace-report")
    gateway = _gateway(db_session, fake)
    chat = await gateway.complete(prompt=_prompt(), ctx=_ctx(org_id, user_id, UsageFeature.CHAT))
    await gateway.complete(prompt=_prompt(), ctx=_ctx(org_id, user_id, UsageFeature.REPORT))

    rows = (
        await db_session.execute(
            select(TokenUsageEvent.feature, TokenUsageEvent.total_tokens, TokenUsageEvent.trace_id)
        )
    ).all()
    by_feature = {feature: total for feature, total, _ in rows}
    assert by_feature[UsageFeature.CHAT] == 15
    assert by_feature[UsageFeature.REPORT] == 25
    # trace_id is the join key from the completion into the usage event
    assert any(trace == chat.trace_id == "trace-chat" for _, _, trace in rows)
