"""ARQ worker entrypoint (composition root): `arq src.worker.WorkerSettings`.

Imports every model module so all tables register on Base.metadata (the worker
queries documents/chunks whose foreign keys target identity tables). The
knowledge domain itself must not import identity's models (import-linter); that
wiring belongs here at the composition root, like src/main.py.
"""

from typing import Any

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.adapters.litellm_gateway import LiteLLMGateway
from src.ai.adapters.openai_embedder import OpenAIEmbedder
from src.ai.dependencies import build_metered_traced_gateway, get_circuit_breaker, get_tracer
from src.ai.gateway import LLMGateway
from src.ai.repository import ModelConfigRepository
from src.conversation import models as _conversation_models  # noqa: F401 - register tables
from src.conversation.worker import summarize_session
from src.identity import models as _identity_models  # noqa: F401 - register identity tables
from src.identity.repository import OrganizationRepository
from src.identity.service import OrgPolicyService
from src.knowledge import models as _knowledge_models  # noqa: F401 - register knowledge tables
from src.knowledge.repository import ChunkRepository
from src.knowledge.service import ChunkRetrievalService
from src.knowledge.worker import (
    ingest_document,
    ingest_startup,
    sweep_storage_outbox,
    sweep_stuck_documents,
)
from src.recommendation import models as _recommendation_models  # noqa: F401 - register tables
from src.reports import models as _reports_models  # noqa: F401 - register report tables
from src.reports.worker import generate_report, sweep_stuck_reports
from src.search.service import SearchService
from src.shared.audit import get_audit_emitter
from src.shared.config import get_settings


def _make_tenant_lister(session: AsyncSession) -> OrgPolicyService:
    """Compose identity's read-only policy service for the sweeper (cross-domain
    wiring belongs at the composition root)."""
    return OrgPolicyService(OrganizationRepository(session))


def _make_gateway(session: AsyncSession) -> LLMGateway:
    """Compose the full gateway (tracing → metering → litellm) for a worker session.
    The provider adapter is imported only here, at the composition root."""
    adapter = LiteLLMGateway(
        settings=get_settings(),
        configs=ModelConfigRepository(session),
        breaker=get_circuit_breaker(),
    )
    return build_metered_traced_gateway(
        inner=adapter, session=session, audit=get_audit_emitter(), tracer=get_tracer()
    )


def _make_retriever(session: AsyncSession) -> SearchService:
    """Compose the permission-aware retriever (search over knowledge) for a worker session —
    the report graph's only retrieval entry. Cross-domain wiring at the composition root."""
    settings = get_settings()
    return SearchService(
        reader=ChunkRetrievalService(ChunkRepository(session), settings),
        embedder=OpenAIEmbedder(settings),
        audit=get_audit_emitter(),
        settings=settings,
    )


async def _on_startup(ctx: dict[str, Any]) -> None:
    await ingest_startup(ctx)
    ctx["tenant_lister_factory"] = _make_tenant_lister
    ctx["embedder"] = OpenAIEmbedder(ctx["settings"])
    ctx["gateway_factory"] = _make_gateway
    ctx["retriever_factory"] = _make_retriever


class WorkerSettings:
    functions = [ingest_document, summarize_session, generate_report]
    cron_jobs = [
        cron(sweep_stuck_documents, minute=set(range(0, 60, 5)), run_at_startup=False),
        cron(sweep_storage_outbox, minute=set(range(0, 60, 5)), run_at_startup=False),
        cron(sweep_stuck_reports, minute=set(range(0, 60, 5)), run_at_startup=False),
    ]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = _on_startup
