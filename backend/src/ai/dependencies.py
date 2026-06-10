"""AI FastAPI dependencies — provide the Embedder and the composed LLM gateway behind
their Protocols. Provider SDKs are imported lazily (only inside adapters), so code that
doesn't use them never loads them."""

from functools import lru_cache
from typing import TYPE_CHECKING

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.adapters.circuit_breaker import CircuitBreaker
from src.ai.embedding import Embedder
from src.ai.gateway import LLMGateway
from src.ai.tracing import TracingPort
from src.shared.audit import AuditEmitter, get_audit_emitter
from src.shared.config import Settings, get_settings
from src.shared.database import get_db_session

if TYPE_CHECKING:
    from src.ai.config_service import AIConfigService
    from src.ai.policy import ModelPolicyService


@lru_cache
def get_embedder() -> Embedder:
    """Process-wide embedder, built from settings on first use. The provider SDK is
    imported lazily (only in the adapter), so non-embedding code never loads it."""
    from src.ai.adapters.openai_embedder import OpenAIEmbedder

    return OpenAIEmbedder(get_settings())


@lru_cache
def get_circuit_breaker() -> CircuitBreaker:
    """Process-wide circuit breaker (in-process state shared across requests)."""
    settings = get_settings()
    return CircuitBreaker(
        failure_threshold=settings.llm_circuit_failure_threshold,
        reset_seconds=settings.llm_circuit_reset_seconds,
    )


@lru_cache
def get_tracer() -> TracingPort:
    """The configured trace exporter — logging (dev) or langfuse (prod). The langfuse
    SDK is imported lazily, only when that exporter is selected."""
    settings = get_settings()
    if settings.tracing_exporter == "langfuse":
        from src.ai.adapters.langfuse_tracer import LangfuseTracer, build_langfuse_client

        return LangfuseTracer(build_langfuse_client(settings))
    from src.ai.tracing import LoggingTracer

    return LoggingTracer()


def build_metered_traced_gateway(
    *,
    inner: LLMGateway,
    session: AsyncSession,
    audit: AuditEmitter,
    tracer: TracingPort,
) -> LLMGateway:
    """Compose the gateway stack once: tracing → metering/budget → inner adapter. Every
    feature receives this composition, so metering, budgets, and tracing cannot be
    bypassed (CLAUDE.md §6)."""
    from src.ai.metering import MeteringGateway
    from src.ai.repository import TokenBudgetRepository, TokenUsageRepository
    from src.ai.tracing import TracingGateway

    metered = MeteringGateway(
        inner=inner,
        budgets=TokenBudgetRepository(session),
        usage=TokenUsageRepository(session),
        audit=audit,
    )
    return TracingGateway(inner=metered, tracer=tracer)


def get_gateway(
    session: AsyncSession = Depends(get_db_session),
    audit: AuditEmitter = Depends(get_audit_emitter),
) -> LLMGateway:
    """Request-scoped gateway: the litellm adapter wrapped in metering + tracing."""
    from src.ai.adapters.litellm_gateway import LiteLLMGateway
    from src.ai.repository import ModelConfigRepository

    adapter = LiteLLMGateway(
        settings=get_settings(),
        configs=ModelConfigRepository(session),
        breaker=get_circuit_breaker(),
    )
    return build_metered_traced_gateway(
        inner=adapter, session=session, audit=audit, tracer=get_tracer()
    )


def get_ai_config_service(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    audit: AuditEmitter = Depends(get_audit_emitter),
) -> "AIConfigService":
    """Owner/admin AI config & budget service, bound to the request session + audit emitter."""
    from src.ai.config_service import AIConfigService
    from src.ai.repository import ModelConfigRepository, TokenBudgetRepository

    return AIConfigService(
        model_configs=ModelConfigRepository(session),
        budgets=TokenBudgetRepository(session),
        audit=audit,
        settings=settings,
    )


def get_model_policy_service(
    session: AsyncSession = Depends(get_db_session),
) -> "ModelPolicyService":
    """Read-only model-policy service (the PII-redaction toggle) for cross-domain consumers."""
    from src.ai.policy import ModelPolicyService
    from src.ai.repository import ModelConfigRepository

    return ModelPolicyService(ModelConfigRepository(session))
