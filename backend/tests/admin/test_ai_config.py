"""AI configuration administration — owner/admin only; model config validated (bad model ref
and empty fallback chain rejected); every change audited; members forbidden."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.models import Role
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.admin.conftest import AdminHarness, auth, seed_org


async def test_model_config_defaults_when_unset(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    resp = await admin_harness.client.get(
        "/api/v1/admin/ai/model-config", headers=auth(org.tokens[Role.ADMIN])
    )
    assert resp.status_code == 200
    assert resp.json()["default_model"] == settings.llm_default_model


async def test_member_forbidden_on_ai_config(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    member = auth(org.tokens[Role.MEMBER])
    assert (
        await admin_harness.client.get("/api/v1/admin/ai/model-config", headers=member)
    ).status_code == 403
    resp = await admin_harness.client.put(
        "/api/v1/admin/ai/budget", json={"limit_tokens": 1000}, headers=member
    )
    assert resp.status_code == 403


async def test_set_model_config_persists_and_is_audited(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    body = {
        "default_model": "openai/gpt-4o-mini",
        "fallback_chain": ["anthropic/claude-3-5-sonnet-latest"],
        "temperature_default": 0.3,
    }
    resp = await admin_harness.client.put(
        "/api/v1/admin/ai/model-config", json=body, headers=auth(org.tokens[Role.OWNER])
    )
    assert resp.status_code == 200 and resp.json()["temperature_default"] == 0.3
    # The change is visible on the next read...
    get = await admin_harness.client.get(
        "/api/v1/admin/ai/model-config", headers=auth(org.tokens[Role.ADMIN])
    )
    assert get.json()["fallback_chain"] == ["anthropic/claude-3-5-sonnet-latest"]
    # ...and audited.
    await set_tenant_context(db_session, org.org_id)
    count = (
        await db_session.execute(
            text("SELECT count(*) FROM audit_events WHERE action = 'ai.model_config_changed'")
        )
    ).scalar_one()
    assert count == 1


async def test_unknown_model_provider_is_rejected(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    resp = await admin_harness.client.put(
        "/api/v1/admin/ai/model-config",
        json={"default_model": "bogus/model", "fallback_chain": ["openai/gpt-4o-mini"]},
        headers=auth(org.tokens[Role.OWNER]),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_MODEL_CONFIG"


async def test_empty_fallback_chain_is_rejected(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    resp = await admin_harness.client.put(
        "/api/v1/admin/ai/model-config",
        json={"default_model": "openai/gpt-4o-mini", "fallback_chain": []},
        headers=auth(org.tokens[Role.OWNER]),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_MODEL_CONFIG"


async def test_set_budget_is_audited(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    resp = await admin_harness.client.put(
        "/api/v1/admin/ai/budget",
        json={"limit_tokens": 500000, "soft_threshold_pct": 75},
        headers=auth(org.tokens[Role.ADMIN]),
    )
    assert resp.status_code == 200 and resp.json()["limit_tokens"] == 500000
    await set_tenant_context(db_session, org.org_id)
    count = (
        await db_session.execute(
            text("SELECT count(*) FROM audit_events WHERE action = 'ai.budget_changed'")
        )
    ).scalar_one()
    assert count == 1


async def test_pii_redaction_toggle_round_trips_and_audits(
    admin_harness: AdminHarness, db_session: AsyncSession, settings: Settings
) -> None:
    org = await seed_org(db_session, settings)
    resp = await admin_harness.client.put(
        "/api/v1/admin/ai/model-config",
        json={
            "default_model": "openai/gpt-4o-mini",
            "fallback_chain": ["anthropic/claude-3-5-sonnet-latest"],
            "pii_redaction_enabled": False,
        },
        headers=auth(org.tokens[Role.OWNER]),
    )
    assert resp.status_code == 200 and resp.json()["pii_redaction_enabled"] is False
    get = await admin_harness.client.get(
        "/api/v1/admin/ai/model-config", headers=auth(org.tokens[Role.ADMIN])
    )
    assert get.json()["pii_redaction_enabled"] is False
