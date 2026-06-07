"""AI schema: forced RLS on every tenant table, and runtime-role CRUD under tenant
context (the Sprint-1 default privileges hold for the new tables). The append-only
and budget tables are exercised as the non-bypassrls app role so RLS is real."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.models import (
    BudgetPeriod,
    ModelConfig,
    TokenBudget,
    TokenUsageEvent,
    TokenUsagePeriod,
    UsageFeature,
)
from src.identity.service import AuthService
from src.shared.database import set_tenant_context

_AI_TABLES = ("model_configs", "token_budgets", "token_usage_events", "token_usage_periods")


@pytest.mark.parametrize("table", _AI_TABLES)
async def test_forced_rls_enabled_on_ai_tables(db_session: AsyncSession, table: str) -> None:
    row = (
        await db_session.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = :t AND relkind = 'r'"
            ),
            {"t": table},
        )
    ).one()
    assert row == (True, True)  # RLS enabled AND forced (CLAUDE.md §4)


async def test_runtime_role_can_crud_ai_rows_under_tenant_context(
    auth_service: AuthService, db_session: AsyncSession
) -> None:
    org = await auth_service.register_organization(
        name="AiOrg",
        slug="ai-org",
        owner_email="o@ai.test",
        full_name="O",
        password="password-123456",
    )
    org_id, user_id = org.organization.id, org.owner.id
    await set_tenant_context(db_session, org_id)

    db_session.add(
        ModelConfig(
            org_id=org_id,
            default_model="azure/gpt-4o",
            fallback_chain=["openai/gpt-4o-mini"],
            updated_by=user_id,
        )
    )
    db_session.add(TokenBudget(org_id=org_id, period=BudgetPeriod.MONTHLY, limit_tokens=1_000_000))
    period_start = datetime(2026, 6, 1, tzinfo=UTC)
    db_session.add(TokenUsagePeriod(org_id=org_id, period_start=period_start, total_tokens=1500))
    db_session.add(
        TokenUsageEvent(
            org_id=org_id,
            user_id=user_id,
            feature=UsageFeature.CHAT,
            model="azure/gpt-4o",
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            trace_id="trace-abc",
            request_id="req-abc",
        )
    )
    await db_session.flush()

    cfg = (await db_session.execute(text("SELECT default_model FROM model_configs"))).scalar_one()
    assert cfg == "azure/gpt-4o"
    total = (
        await db_session.execute(text("SELECT total_tokens FROM token_usage_periods"))
    ).scalar_one()
    assert total == 1500


async def test_rls_hides_ai_rows_from_other_org(
    auth_service: AuthService, db_session: AsyncSession
) -> None:
    org = await auth_service.register_organization(
        name="AiOrgB",
        slug="ai-org-b",
        owner_email="o@aib.test",
        full_name="O",
        password="password-123456",
    )
    org_id = org.organization.id
    await set_tenant_context(db_session, org_id)
    db_session.add(TokenBudget(org_id=org_id, period=BudgetPeriod.MONTHLY, limit_tokens=42))
    await db_session.flush()
    assert (await db_session.execute(text("SELECT count(*) FROM token_budgets"))).scalar_one() == 1

    await set_tenant_context(db_session, org.owner.id)  # any other org_id → RLS hides the row
    assert (await db_session.execute(text("SELECT count(*) FROM token_budgets"))).scalar_one() == 0
