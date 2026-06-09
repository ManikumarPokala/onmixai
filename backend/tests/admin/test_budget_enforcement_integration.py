"""The Phase-6 Task-5 exit criterion: an admin lowering the budget through the HTTP surface
takes effect on the very NEXT metered gateway call — same process, no restart, no cache. The
metering gateway re-reads the budget row every call (ai/metering._pre_check), and both the admin
write and the gateway read share the request session, so the freshly-written cap blocks at once.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.gateway import BudgetExceededError
from src.identity.models import Role
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.admin.conftest import AdminHarness, auth, seed_org
from tests.ai.test_metering import _ctx, _gateway, _prompt
from tests.fakes.fake_gateway import FakeGateway


async def test_admin_lowering_budget_blocks_the_next_call_live(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    await set_tenant_context(db_session, org.org_id)
    owner_id = org.user_ids[Role.OWNER]
    admin = auth(org.tokens[Role.ADMIN])

    fake = FakeGateway()
    for _ in range(2):
        fake.queue_completion(prompt_tokens=400, completion_tokens=200)  # 600 tokens / call
    gateway = _gateway(db_session, fake)

    # A generous budget lets the first call through (0 used < 1_000_000), spending 600 tokens.
    high = await admin_harness.client.put(
        "/api/v1/admin/ai/budget", json={"limit_tokens": 1_000_000}, headers=admin
    )
    assert high.status_code == 200
    await gateway.complete(prompt=_prompt(), ctx=_ctx(org.org_id, owner_id))
    assert len(fake.calls) == 1

    # The admin lowers the cap below what is already spent — through the same HTTP surface.
    low = await admin_harness.client.put(
        "/api/v1/admin/ai/budget", json={"limit_tokens": 500}, headers=admin
    )
    assert low.status_code == 200

    # The very next call is blocked before reaching the provider — no restart, no stale cache.
    with pytest.raises(BudgetExceededError):
        await gateway.complete(prompt=_prompt(), ctx=_ctx(org.org_id, owner_id))
    assert len(fake.calls) == 1  # the blocked call never hit the provider → zero extra spend
