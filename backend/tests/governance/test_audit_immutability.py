"""audit_events is immutable at the DATABASE for the runtime role (migration 0009).

Every fixture here connects as the non-superuser runtime role (the conftest db_session), exactly
as the application does. The runtime role may INSERT and SELECT audit rows, but UPDATE and DELETE
are revoked — so a compromised app process can append to the audit trail but never rewrite or
erase it. retention_policies is forced-RLS like every tenant table.
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.models import Organization, Role, User
from src.shared.audit import AuditEvent
from src.shared.database import set_tenant_context


async def _seed_org_user(session: AsyncSession) -> tuple[UUID, UUID]:
    org_id, user_id = uuid4(), uuid4()
    await set_tenant_context(session, org_id)
    session.add(Organization(id=org_id, name="Org", slug=f"org-{org_id}"))
    await session.flush()
    session.add(
        User(
            id=user_id,
            org_id=org_id,
            email=f"u-{user_id}@x.test",
            password_hash="x",
            full_name="U",
            role=Role.OWNER,
        )
    )
    await session.flush()
    return org_id, user_id


async def test_runtime_role_can_insert_and_select_audit(db_session: AsyncSession) -> None:
    org_id, user_id = await _seed_org_user(db_session)
    db_session.add(
        AuditEvent(org_id=org_id, actor_user_id=user_id, action="test.created", event_metadata={})
    )
    await db_session.flush()  # INSERT must succeed for the runtime role
    count = (
        await db_session.execute(
            text("SELECT count(*) FROM audit_events WHERE org_id = :org"), {"org": str(org_id)}
        )
    ).scalar_one()
    assert count == 1  # SELECT (org-scoped) must succeed


async def test_runtime_role_cannot_update_audit(db_session: AsyncSession) -> None:
    org_id, user_id = await _seed_org_user(db_session)
    db_session.add(
        AuditEvent(org_id=org_id, actor_user_id=user_id, action="test.created", event_metadata={})
    )
    await db_session.flush()
    with pytest.raises(Exception) as exc:  # noqa: PT011 — asserting the DB permission message
        await db_session.execute(text("UPDATE audit_events SET action = 'tampered'"))
    assert "permission denied" in str(exc.value).lower()


async def test_runtime_role_cannot_delete_audit(db_session: AsyncSession) -> None:
    org_id, user_id = await _seed_org_user(db_session)
    db_session.add(
        AuditEvent(org_id=org_id, actor_user_id=user_id, action="test.created", event_metadata={})
    )
    await db_session.flush()
    with pytest.raises(Exception) as exc:  # noqa: PT011
        await db_session.execute(text("DELETE FROM audit_events"))
    assert "permission denied" in str(exc.value).lower()


async def test_retention_policies_is_forced_rls(db_session: AsyncSession) -> None:
    row = (
        await db_session.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = 'retention_policies'"
            )
        )
    ).one()
    assert tuple(row) == (True, True)  # RLS enabled AND forced (t|t)


async def test_audit_events_is_forced_rls(db_session: AsyncSession) -> None:
    row = (
        await db_session.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = 'audit_events'"
            )
        )
    ).one()
    assert tuple(row) == (True, True)
