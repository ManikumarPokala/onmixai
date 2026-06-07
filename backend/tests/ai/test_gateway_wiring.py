"""The composed gateway: tracing → metering → inner. One completion through the
wired stack both records a trace and meters the usage (neither can be bypassed)."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.dependencies import build_metered_traced_gateway
from src.ai.models import TokenUsageEvent
from src.identity.service import AuthService
from src.shared.audit import AuditEmitter
from src.shared.database import set_tenant_context
from tests.ai.test_metering import _ctx, _org, _prompt
from tests.ai.test_tracing import _RecordingTracer
from tests.fakes.fake_gateway import FakeGateway


async def test_composition_meters_and_traces_one_completion(
    auth_service: AuthService, db_session: AsyncSession
) -> None:
    org_id, user_id = await _org(auth_service)
    await set_tenant_context(db_session, org_id)
    inner = FakeGateway()
    inner.queue_completion(prompt_tokens=30, completion_tokens=20, trace_id="t1")
    tracer = _RecordingTracer()
    gateway = build_metered_traced_gateway(
        inner=inner, session=db_session, audit=AuditEmitter(), tracer=tracer
    )

    completion = await gateway.complete(prompt=_prompt(), ctx=_ctx(org_id, user_id))

    # tracing layer ran
    assert len(tracer.traces) == 1 and tracer.traces[0].trace_id == "t1"
    # metering layer ran (same call, one transaction)
    total = (await db_session.execute(select(func.sum(TokenUsageEvent.total_tokens)))).scalar_one()
    assert total == completion.total_tokens == 50
