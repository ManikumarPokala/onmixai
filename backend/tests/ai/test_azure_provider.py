"""Azure OpenAI provider — AZURE-READY, verified against a real deployment SEPARATELY (run
``scripts/azure_smoke.py``). These tests prove everything up to the credential line WITHOUT a live
endpoint: an ``azure/<logical>`` model ref resolves the Azure connection + deployment mapping and
routes through the same litellm adapter as OpenAI, and the cross-cutting concerns (fallback,
budget pre-block, metering, tracing) all exercise identically on the Azure path — using the
deterministic injected provider call standing in as an Azure-shaped provider. No network, no creds.
"""

from typing import Any
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.dependencies import build_metered_traced_gateway
from src.ai.gateway import BudgetExceededError, GatewayContext
from src.ai.models import BudgetPeriod, TokenBudget, TokenUsageEvent, UsageFeature
from src.ai.tracing import CompletionTrace, TracingPort
from src.identity.models import Organization, Role, User
from src.shared.audit import AuditEmitter
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.ai.conftest import NoModelConfig
from tests.ai.test_litellm_adapter import _ctx, _FakeAcompletion, _gateway, _prompt, _resp

_AZURE_BASE = dict(
    azure_openai_endpoint="https://contoso.openai.azure.com",
    azure_openai_api_version="2024-06-01",
    azure_openai_api_key="azure-secret",
)


def _azure_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = dict(
        _env_file=None,
        env="test",
        database_url="postgresql+asyncpg://u:p@localhost:5432/db",
        jwt_secret="x" * 40,
        storage_endpoint="http://localhost:9000",
        storage_access_key="a",
        storage_secret_key="s",
        storage_bucket="b",
        redis_url="redis://localhost:6379/0",
        embedding_dimension=8,
        llm_default_model="azure/gpt-4o-mini",
        azure_deployment_map={"gpt-4o-mini": "prod-4o-mini"},
        **_AZURE_BASE,
    )
    base.update(overrides)
    return Settings(**base)


# --- routing + config resolution (no DB, no network) ---


async def test_azure_ref_resolves_deployment_and_connection() -> None:
    settings = _azure_settings()
    fake = _FakeAcompletion([_resp(content="hi from azure")])
    gateway = _gateway(settings, fake)
    completion = await gateway.complete(prompt=_prompt(), ctx=_ctx())

    call = fake.calls[0]
    assert call["model"] == "azure/prod-4o-mini"  # logical → deployment via the map
    assert call["api_base"] == "https://contoso.openai.azure.com"
    assert call["api_version"] == "2024-06-01"
    assert call["api_key"] == "azure-secret"
    # Metering/tracing record the LOGICAL ref, not the deployment.
    assert completion.model_used == "azure/gpt-4o-mini"


async def test_unmapped_azure_logical_name_falls_back_to_identity() -> None:
    settings = _azure_settings(llm_default_model="azure/gpt-4o", azure_deployment_map={})
    fake = _FakeAcompletion([_resp()])
    await _gateway(settings, fake).complete(prompt=_prompt(), ctx=_ctx())
    assert fake.calls[0]["model"] == "azure/gpt-4o"  # no map entry → logical == deployment


async def test_openai_path_is_unchanged_by_azure_plumbing() -> None:
    # A non-azure ref keeps the existing OpenAI-compatible behaviour (no azure params leak in).
    from tests.ai.conftest import llm_settings

    settings = llm_settings("http://llm-stub:9000", default_model="openai/okmodel")
    fake = _FakeAcompletion([_resp()])
    await _gateway(settings, fake).complete(prompt=_prompt(), ctx=_ctx())
    call = fake.calls[0]
    assert call["model"] == "openai/okmodel"
    assert call["api_base"] == "http://llm-stub:9000"
    assert "api_version" not in call


# --- fail-fast configuration validation ---


@pytest.mark.parametrize(
    ("missing", "needle"),
    [
        ("azure_openai_endpoint", "AZURE_OPENAI_ENDPOINT"),
        ("azure_openai_api_version", "AZURE_OPENAI_API_VERSION"),
        ("azure_openai_api_key", "AZURE_OPENAI_API_KEY"),
    ],
)
def test_azure_model_without_full_config_fails_fast(missing: str, needle: str) -> None:
    with pytest.raises(ValueError, match=needle):
        _azure_settings(**{missing: None})


def test_no_azure_model_means_azure_config_is_optional() -> None:
    # An all-OpenAI config needs none of the azure fields — the guard only bites when azure is used.
    from tests.ai.conftest import llm_settings

    llm_settings("http://llm-stub:9000", default_model="openai/okmodel")  # must not raise


def test_prod_rejects_a_stub_azure_endpoint() -> None:
    with pytest.raises(ValueError, match="stub/localhost"):
        _azure_settings(
            env="prod",
            azure_openai_endpoint="http://localhost:9000",
            llm_fallback_chain=["azure/gpt-4o-mini"],
            jwt_secret="z" * 40,
            tracing_logging_allowed_in_prod=True,
        )


# --- fallback exercises on the azure path ---


async def test_fallback_advances_along_the_azure_chain() -> None:
    settings = _azure_settings(
        llm_default_model="azure/primary",
        llm_fallback_chain=["azure/secondary"],
        azure_deployment_map={"primary": "dep-primary", "secondary": "dep-secondary"},
    )
    import litellm

    # Primary azure deployment fails every attempt (retryable) → chain advances to the secondary.
    outcomes: list[Any] = [
        litellm.APIConnectionError("azure down", "azure/dep-primary", "azure")
    ] * 5
    outcomes.append(_resp(content="from secondary"))
    fake = _FakeAcompletion(outcomes)
    completion = await _gateway(settings, fake).complete(prompt=_prompt(), ctx=_ctx())

    used_models = [c["model"] for c in fake.calls]
    assert "azure/dep-primary" in used_models and used_models[-1] == "azure/dep-secondary"
    assert completion.model_used == "azure/secondary"  # logical ref of the model that answered


# --- budget + metering + tracing exercise on the azure path (full stack, DB) ---


class _CapturingTracer(TracingPort):
    def __init__(self) -> None:
        self.traces: list[CompletionTrace] = []

    def span(self, name: str, **attrs: object):  # type: ignore[no-untyped-def]  # noqa: ARG002
        from contextlib import nullcontext

        return nullcontext()

    def record_completion(self, trace: CompletionTrace) -> None:
        self.traces.append(trace)


async def _seed_org(session: AsyncSession) -> tuple[Any, Any]:
    org_id, user_id = uuid4(), uuid4()
    await set_tenant_context(session, org_id)
    session.add(Organization(id=org_id, name="Az", slug=f"az-{org_id}"))
    await session.flush()
    session.add(  # the metered usage event FK-references the actor
        User(
            id=user_id,
            org_id=org_id,
            email=f"az-{user_id}@x.test",
            password_hash="x",
            full_name="Az",
            role=Role.OWNER,
        )
    )
    await session.flush()
    return org_id, user_id


def _azure_db_settings() -> Settings:
    return _azure_settings(
        database_url="postgresql+asyncpg://u:p@localhost:5432/db",
        azure_openai_api_key=SecretStr("azure-secret"),
    )


async def test_budget_pre_block_fires_before_any_azure_call(db_session: AsyncSession) -> None:
    org_id, user_id = await _seed_org(db_session)
    db_session.add(TokenBudget(org_id=org_id, period=BudgetPeriod.MONTHLY, limit_tokens=0))
    await db_session.flush()
    fake = _FakeAcompletion([_resp()])
    inner = _gateway(_azure_db_settings(), fake, configs=NoModelConfig())
    gateway = build_metered_traced_gateway(
        inner=inner, session=db_session, audit=AuditEmitter(), tracer=_CapturingTracer()
    )
    ctx = GatewayContext(org_id=org_id, user_id=user_id, feature=UsageFeature.CHAT, request_id="r")
    with pytest.raises(BudgetExceededError):
        await gateway.complete(prompt=_prompt(), ctx=ctx)
    assert len(fake.calls) == 0  # blocked before the azure provider was ever called → zero spend


async def test_metering_and_tracing_record_the_azure_model(db_session: AsyncSession) -> None:
    org_id, user_id = await _seed_org(db_session)
    fake = _FakeAcompletion([_resp(content="grounded answer", pt=12, ct=5)])
    inner = _gateway(_azure_db_settings(), fake, configs=NoModelConfig())
    tracer = _CapturingTracer()
    gateway = build_metered_traced_gateway(
        inner=inner, session=db_session, audit=AuditEmitter(), tracer=tracer
    )
    ctx = GatewayContext(org_id=org_id, user_id=user_id, feature=UsageFeature.CHAT, request_id="r")
    await gateway.complete(prompt=_prompt(), ctx=ctx)

    # The trace records the azure logical model...
    assert tracer.traces[-1].as_attributes()["model_used"] == "azure/gpt-4o-mini"
    # ...and the metered usage event is reconciled against the same azure model.
    event = (
        await db_session.execute(select(TokenUsageEvent).where(TokenUsageEvent.org_id == org_id))
    ).scalar_one()
    assert event.model == "azure/gpt-4o-mini" and event.total_tokens == 17
