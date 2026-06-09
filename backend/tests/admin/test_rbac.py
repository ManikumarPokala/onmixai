"""require_admin gate: owner/admin reach /admin endpoints; everyone else is a 403 (no 404 — the
route exists, the actor just lacks the role). Exhaustive over the admin route table (one route
in Task 2; Task 10 enumerates the full table)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.models import Role
from src.shared.config import Settings
from tests.admin.conftest import AdminHarness, auth, seed_org

_ADMIN_ROUTES = [("GET", "/api/v1/admin/audit")]


async def test_member_is_forbidden_on_every_admin_route(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    for method, path in _ADMIN_ROUTES:
        resp = await admin_harness.client.request(
            method, path, headers=auth(org.tokens[Role.MEMBER])
        )
        assert resp.status_code == 403, f"{method} {path}"
        assert resp.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize("role", [Role.OWNER, Role.ADMIN])
async def test_owner_and_admin_are_allowed(
    role: Role, admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    resp = await admin_harness.client.get("/api/v1/admin/audit", headers=auth(org.tokens[role]))
    assert resp.status_code == 200


async def test_unauthenticated_is_rejected(admin_harness: AdminHarness) -> None:
    resp = await admin_harness.client.get("/api/v1/admin/audit")
    assert resp.status_code == 401
