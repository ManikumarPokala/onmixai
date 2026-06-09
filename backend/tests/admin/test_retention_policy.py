"""Retention-policy administration — owner/admin only; unset means retain-by-default (null
windows); setting windows persists and is audited; one org's policy never leaks to another."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.models import Role
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.admin.conftest import AdminHarness, auth, seed_org


async def test_member_forbidden_on_retention_policy(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    member = auth(org.tokens[Role.MEMBER])
    assert (
        await admin_harness.client.get("/api/v1/admin/retention-policy", headers=member)
    ).status_code == 403
    resp = await admin_harness.client.put(
        "/api/v1/admin/retention-policy", json={"audit_retention_days": 90}, headers=member
    )
    assert resp.status_code == 403


async def test_unset_policy_is_retain_by_default(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    resp = await admin_harness.client.get(
        "/api/v1/admin/retention-policy", headers=auth(org.tokens[Role.ADMIN])
    )
    assert resp.status_code == 200
    # Null windows → the Task-7 purge job retains everything (the safe default).
    assert resp.json() == {"audit_retention_days": None, "conversation_retention_days": None}


async def test_set_policy_persists_and_is_audited(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    resp = await admin_harness.client.put(
        "/api/v1/admin/retention-policy",
        json={"audit_retention_days": 365, "conversation_retention_days": 30},
        headers=auth(org.tokens[Role.OWNER]),
    )
    assert resp.status_code == 200
    assert resp.json() == {"audit_retention_days": 365, "conversation_retention_days": 30}
    # Visible on the next read...
    get = await admin_harness.client.get(
        "/api/v1/admin/retention-policy", headers=auth(org.tokens[Role.ADMIN])
    )
    assert get.json()["audit_retention_days"] == 365
    # ...and audited.
    await set_tenant_context(db_session, org.org_id)
    count = (
        await db_session.execute(
            text("SELECT count(*) FROM audit_events WHERE action = 'retention_policy_changed'")
        )
    ).scalar_one()
    assert count == 1


async def test_policy_is_one_per_org_and_isolated(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org_a = await seed_org(db_session, settings, slug="a")
    org_b = await seed_org(db_session, settings, slug="b")
    await admin_harness.client.put(
        "/api/v1/admin/retention-policy",
        json={"conversation_retention_days": 7},
        headers=auth(org_a.tokens[Role.OWNER]),
    )
    # Org B never set a policy → it still reads retain-by-default, unaffected by org A.
    resp_b = await admin_harness.client.get(
        "/api/v1/admin/retention-policy", headers=auth(org_b.tokens[Role.ADMIN])
    )
    assert resp_b.json() == {"audit_retention_days": None, "conversation_retention_days": None}
