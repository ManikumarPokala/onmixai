"""Recommendation FastAPI dependencies — compose RecommendationService from its repository
and the recommendation pipeline. The pipeline's retriever is search's SearchService (the ONLY
retrieval entry) and its gateway is the metered+traced composition every feature shares.
Constructor injection only; the per-user rate-limit key is set here (it needs the actor)."""

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.dependencies import get_gateway
from src.ai.gateway import LLMGateway
from src.ai.prompt_registry import PromptRegistry, get_prompt_registry
from src.identity.dependencies import get_current_user
from src.identity.schemas import AuthContext
from src.recommendation.pipeline import RecommendationPipeline
from src.recommendation.repository import RecommendationRepository
from src.recommendation.service import RecommendationService
from src.search.dependencies import get_search_service
from src.search.service import SearchService
from src.shared.audit import AuditEmitter, get_audit_emitter
from src.shared.config import Settings, get_settings
from src.shared.database import get_db_session


def get_recommendation_service(
    session: AsyncSession = Depends(get_db_session),
    retriever: SearchService = Depends(get_search_service),
    gateway: LLMGateway = Depends(get_gateway),
    registry: PromptRegistry = Depends(get_prompt_registry),
    audit: AuditEmitter = Depends(get_audit_emitter),
    settings: Settings = Depends(get_settings),
) -> RecommendationService:
    pipeline = RecommendationPipeline(
        retriever=retriever, gateway=gateway, registry=registry, settings=settings
    )
    return RecommendationService(
        repository=RecommendationRepository(session),
        pipeline=pipeline,
        audit=audit,
        settings=settings,
    )


async def set_user_scoped_key(
    request: Request, actor: AuthContext = Depends(get_current_user)
) -> None:
    """Dependency: key the recommendation rate limit on the authenticated user (not IP). The
    shared limiter reads ``request.state.rate_limit_key``."""
    request.state.rate_limit_key = f"recommendation:{actor.user_id}"
