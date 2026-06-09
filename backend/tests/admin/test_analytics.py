"""GET /api/v1/admin/analytics/usage — org-scoped aggregates over metering, documents, and
audit; owner/admin only; tokens windowed, documents current."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.models import TokenUsageEvent, UsageFeature
from src.identity.models import Role
from src.knowledge.models import Collection, Document, DocumentStatus
from src.shared.audit import AuditEvent
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.admin.conftest import AdminHarness, auth, seed_org

_WINDOW = datetime(2026, 6, 1, tzinfo=UTC)


async def _usage_event(
    session: AsyncSession,
    org_id: UUID,
    user_id: UUID,
    feature: UsageFeature,
    tokens: int,
    at: datetime,
) -> None:
    session.add(
        TokenUsageEvent(
            org_id=org_id,
            user_id=user_id,
            feature=feature,
            model="m",
            prompt_tokens=tokens,
            completion_tokens=0,
            total_tokens=tokens,
            trace_id="t",
            request_id="r",
            created_at=at,
        )
    )


async def test_usage_aggregates(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    owner, admin = org.user_ids[Role.OWNER], org.user_ids[Role.ADMIN]
    await set_tenant_context(db_session, org.org_id)

    at = _WINDOW + timedelta(hours=1)
    await _usage_event(db_session, org.org_id, owner, UsageFeature.CHAT, 100, at)
    await _usage_event(db_session, org.org_id, owner, UsageFeature.CHAT, 200, at)
    await _usage_event(db_session, org.org_id, owner, UsageFeature.REPORT, 50, at)

    collection_id = uuid4()
    db_session.add(Collection(id=collection_id, org_id=org.org_id, name="c", created_by=owner))
    await db_session.flush()
    db_session.add(
        Document(
            id=uuid4(),
            org_id=org.org_id,
            collection_id=collection_id,
            filename="d.txt",
            content_type="text/plain",
            size_bytes=1000,
            storage_key="k",
            content_hash="h",
            status=DocumentStatus.READY,
            created_by=owner,
        )
    )
    for actor_id in (owner, admin):
        db_session.add(
            AuditEvent(
                org_id=org.org_id,
                actor_user_id=actor_id,
                action="search.executed",
                event_metadata={},
                created_at=at,
            )
        )
    await db_session.flush()

    resp = await admin_harness.client.get(
        f"/api/v1/admin/analytics/usage?start={_WINDOW.isoformat()}"
        f"&end={(_WINDOW + timedelta(days=1)).isoformat()}",
        headers=auth(org.tokens[Role.ADMIN]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tokens_by_feature"] == {"chat": 300, "report": 50}
    assert body["tokens_total"] == 350
    assert body["document_count"] == 1 and body["storage_bytes"] == 1000
    assert body["search_count"] == 2
    assert body["active_users"] == 2


async def test_member_is_forbidden(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    resp = await admin_harness.client.get(
        "/api/v1/admin/analytics/usage", headers=auth(org.tokens[Role.MEMBER])
    )
    assert resp.status_code == 403


async def test_org_scoped(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org_a = await seed_org(db_session, settings, slug="a")
    org_b = await seed_org(db_session, settings, slug="b")
    at = _WINDOW + timedelta(hours=1)
    await set_tenant_context(db_session, org_b.org_id)
    await _usage_event(
        db_session, org_b.org_id, org_b.user_ids[Role.OWNER], UsageFeature.CHAT, 999, at
    )
    await db_session.flush()

    resp = await admin_harness.client.get(
        f"/api/v1/admin/analytics/usage?start={_WINDOW.isoformat()}"
        f"&end={(_WINDOW + timedelta(days=1)).isoformat()}",
        headers=auth(org_a.tokens[Role.OWNER]),
    )
    assert resp.json()["tokens_total"] == 0  # org A sees none of org B's usage
