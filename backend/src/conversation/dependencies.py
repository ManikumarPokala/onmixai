"""Conversation FastAPI dependencies — compose ChatService from its repositories and the
grounded pipeline. Constructor injection only; the service never builds its own deps.

The pipeline's retriever is search's SearchService (the ONLY retrieval entry point — no
new retrieval surface), and its gateway is the metered+traced composition every feature
shares. The per-user rate-limit key is set here (it needs the authenticated actor), and
read by the shared limiter's key func — so ``shared`` never imports a domain.
"""

from typing import TYPE_CHECKING

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.dependencies import get_gateway
from src.ai.gateway import LLMGateway
from src.ai.policy import ModelPolicyService
from src.ai.prompt_registry import PromptRegistry, get_prompt_registry
from src.ai.repository import ModelConfigRepository
from src.conversation.pipeline import GroundedAnswerPipeline
from src.conversation.repository import (
    ChatMessageRepository,
    ChatSessionRepository,
    MessageFeedbackRepository,
    SessionSummaryRepository,
)
from src.conversation.service import ChatService
from src.identity.dependencies import get_current_user
from src.identity.schemas import AuthContext
from src.search.dependencies import get_search_service
from src.search.service import SearchService
from src.shared.audit import AuditEmitter, get_audit_emitter
from src.shared.config import Settings, get_settings
from src.shared.database import get_db_session

if TYPE_CHECKING:
    from src.conversation.curation_service import FeedbackCurationService


def get_chat_service(
    session: AsyncSession = Depends(get_db_session),
    retriever: SearchService = Depends(get_search_service),
    gateway: LLMGateway = Depends(get_gateway),
    registry: PromptRegistry = Depends(get_prompt_registry),
    audit: AuditEmitter = Depends(get_audit_emitter),
    settings: Settings = Depends(get_settings),
) -> ChatService:
    pipeline = GroundedAnswerPipeline(
        retriever=retriever,
        gateway=gateway,
        registry=registry,
        settings=settings,
    )
    return ChatService(
        sessions=ChatSessionRepository(session),
        messages=ChatMessageRepository(session),
        feedback=MessageFeedbackRepository(session),
        summaries=SessionSummaryRepository(session),
        pipeline=pipeline,
        pii_policy=ModelPolicyService(ModelConfigRepository(session)),
        audit=audit,
        settings=settings,
    )


async def set_user_scoped_key(
    request: Request, actor: AuthContext = Depends(get_current_user)
) -> None:
    """Dependency: key the chat rate limit on the authenticated user (not IP), so the
    30/min cap is per-account. The shared limiter reads ``request.state.rate_limit_key``."""
    request.state.rate_limit_key = f"chat:{actor.user_id}"


def get_feedback_curation_service(
    session: AsyncSession = Depends(get_db_session),
    audit: AuditEmitter = Depends(get_audit_emitter),
    settings: Settings = Depends(get_settings),
) -> "FeedbackCurationService":
    """Owner/admin feedback→golden curation, bound to the request session + audit emitter."""
    from src.ai.guardrails.pii import PIIRedactor
    from src.conversation.curation_service import FeedbackCurationService
    from src.conversation.repository import ChatMessageRepository, GoldenCandidateRepository

    return FeedbackCurationService(
        candidates=GoldenCandidateRepository(session),
        messages=ChatMessageRepository(session),
        redactor=PIIRedactor(),
        audit=audit,
        settings=settings,
    )
