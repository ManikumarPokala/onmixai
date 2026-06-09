"""GET /api/v1/admin/audit — owner/admin-only, org-scoped, filtered, keyset-paginated, and
itself audited (admin.audit_viewed). Cross-org rows are never returned (RLS + the org predicate)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.models import Role
from src.shared.audit import AuditEvent
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.admin.conftest import AdminHarness, auth, seed_org


async def _add_event(
    session: AsyncSession,
    org_id: UUID,
    actor_id: UUID,
    action: str,
    *,
    created_at: datetime | None = None,
    resource_type: str | None = None,
) -> None:
    await set_tenant_context(session, org_id)
    event = AuditEvent(
        org_id=org_id,
        actor_user_id=actor_id,
        action=action,
        resource_type=resource_type,
        event_metadata={},
    )
    if created_at is not None:
        event.created_at = created_at
    session.add(event)
    await session.flush()


async def test_admin_lists_only_its_org_audit_events(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org_a = await seed_org(db_session, settings, slug="a")
    org_b = await seed_org(db_session, settings, slug="b")
    owner_a = org_a.user_ids[Role.OWNER]
    await _add_event(db_session, org_a.org_id, owner_a, "collection.created")
    await _add_event(db_session, org_a.org_id, owner_a, "report.created")
    await _add_event(db_session, org_b.org_id, org_b.user_ids[Role.OWNER], "report.created")

    resp = await admin_harness.client.get(
        "/api/v1/admin/audit", headers=auth(org_a.tokens[Role.ADMIN])
    )
    assert resp.status_code == 200
    actions = [e["action"] for e in resp.json()["events"]]
    assert sorted(actions) == ["collection.created", "report.created"]  # only org A's two


async def test_filter_by_action(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    owner = org.user_ids[Role.OWNER]
    await _add_event(db_session, org.org_id, owner, "collection.created")
    await _add_event(db_session, org.org_id, owner, "report.created")

    resp = await admin_harness.client.get(
        "/api/v1/admin/audit?action=report.created", headers=auth(org.tokens[Role.OWNER])
    )
    actions = [e["action"] for e in resp.json()["events"]]
    assert actions == ["report.created"]


async def test_admin_viewing_audit_is_itself_audited(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    await admin_harness.client.get("/api/v1/admin/audit", headers=auth(org.tokens[Role.ADMIN]))
    await set_tenant_context(db_session, org.org_id)
    count = (
        await db_session.execute(
            text("SELECT count(*) FROM audit_events WHERE action = 'admin.audit_viewed'")
        )
    ).scalar_one()
    assert count == 1  # the view wrote its own audit row


async def test_keyset_pagination(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    owner = org.user_ids[Role.OWNER]
    base = datetime(2026, 6, 1, tzinfo=UTC)
    for i in range(3):
        await _add_event(
            db_session, org.org_id, owner, f"a.{i}", created_at=base + timedelta(minutes=i)
        )

    first = await admin_harness.client.get(
        "/api/v1/admin/audit?limit=2", headers=auth(org.tokens[Role.OWNER])
    )
    body = first.json()
    assert len(body["events"]) == 2 and body["next_cursor"] is not None
    assert [e["action"] for e in body["events"]] == ["a.2", "a.1"]  # newest first

    second = await admin_harness.client.get(
        f"/api/v1/admin/audit?limit=2&cursor={body['next_cursor']}",
        headers=auth(org.tokens[Role.OWNER]),
    )
    rest = [e["action"] for e in second.json()["events"]]
    assert rest == ["a.0"] and second.json()["next_cursor"] is None


async def test_invalid_cursor_is_422(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    resp = await admin_harness.client.get(
        "/api/v1/admin/audit?cursor=not-a-cursor", headers=auth(org.tokens[Role.OWNER])
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_CURSOR"
