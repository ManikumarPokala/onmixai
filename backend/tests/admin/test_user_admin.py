"""User & organization administration — owner/admin only; role/deactivation rules enforced;
deactivation kills sessions immediately; every mutation audited; cross-org users invisible (404)."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.models import Role
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.admin.conftest import AdminHarness, auth, seed_org


async def test_member_forbidden_on_user_admin(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    member = auth(org.tokens[Role.MEMBER])
    assert (
        await admin_harness.client.get("/api/v1/admin/users", headers=member)
    ).status_code == 403
    target = org.user_ids[Role.MEMBER]
    resp = await admin_harness.client.post(
        f"/api/v1/admin/users/{target}/deactivate", headers=member
    )
    assert resp.status_code == 403


async def test_deactivation_invalidates_the_session_immediately(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    member_id = org.user_ids[Role.MEMBER]
    member = auth(org.tokens[Role.MEMBER])
    # The member can use a guarded route before deactivation.
    assert (
        await admin_harness.client.get("/api/v1/chat/sessions", headers=member)
    ).status_code == 200
    # Admin deactivates them...
    resp = await admin_harness.client.post(
        f"/api/v1/admin/users/{member_id}/deactivate", headers=auth(org.tokens[Role.ADMIN])
    )
    assert resp.status_code == 200 and resp.json()["is_active"] is False
    # ...and their next request is rejected (inactive user).
    assert (
        await admin_harness.client.get("/api/v1/chat/sessions", headers=member)
    ).status_code == 401


async def test_admin_cannot_mint_owner(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    resp = await admin_harness.client.patch(
        f"/api/v1/admin/users/{org.user_ids[Role.MEMBER]}/role",
        json={"role": "owner"},
        headers=auth(org.tokens[Role.ADMIN]),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


async def test_cannot_demote_the_last_owner(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    resp = await admin_harness.client.patch(
        f"/api/v1/admin/users/{org.user_ids[Role.OWNER]}/role",
        json={"role": "member"},
        headers=auth(org.tokens[Role.OWNER]),
    )
    assert resp.status_code == 403


async def test_owner_promotes_member_and_it_is_audited(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    member_id = org.user_ids[Role.MEMBER]
    resp = await admin_harness.client.patch(
        f"/api/v1/admin/users/{member_id}/role",
        json={"role": "admin"},
        headers=auth(org.tokens[Role.OWNER]),
    )
    assert resp.status_code == 200 and resp.json()["role"] == "admin"
    await set_tenant_context(db_session, org.org_id)
    count = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM audit_events "
                "WHERE action = 'user.role_changed' AND resource_id = :rid"
            ),
            {"rid": member_id},
        )
    ).scalar_one()
    assert count == 1


async def test_cross_org_user_is_invisible(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org_a = await seed_org(db_session, settings, slug="a")
    org_b = await seed_org(db_session, settings, slug="b")
    resp = await admin_harness.client.post(
        f"/api/v1/admin/users/{org_b.user_ids[Role.MEMBER]}/deactivate",
        headers=auth(org_a.tokens[Role.ADMIN]),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "USER_NOT_FOUND"


async def test_update_organization_profile(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    resp = await admin_harness.client.patch(
        "/api/v1/admin/organization",
        json={"name": "Renamed Inc"},
        headers=auth(org.tokens[Role.ADMIN]),
    )
    assert resp.status_code == 200 and resp.json()["name"] == "Renamed Inc"
