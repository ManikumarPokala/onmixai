"""Rolling session summary — generated through the gateway and persisted with a
compare-and-set on ``through_seq``. Best-effort: a gateway failure (budget block,
provider outage) skips the summary and is logged, never failing the session. Idempotent:
a stale/out-of-order job cannot overwrite a fresher summary (the CAS in the repository).
"""

from uuid import UUID

import structlog

from src.ai.gateway import GatewayContext, LLMGateway
from src.ai.models import UsageFeature
from src.ai.prompt_registry import PromptRegistry
from src.conversation.models import ChatMessage
from src.conversation.repository import SessionSummaryRepository
from src.shared.errors import AppError

_logger = structlog.get_logger("conversation.summary")


def _transcript(messages: list[ChatMessage]) -> str:
    """Render messages into a plain transcript for the summarizer. O(m)."""
    return "\n".join(f"{message.role.value}: {message.content}" for message in messages)


async def update_session_summary(
    *,
    org_id: UUID,
    owner_user_id: UUID,
    session_id: UUID,
    through_seq: int,
    messages: list[ChatMessage],
    gateway: LLMGateway,
    summaries: SessionSummaryRepository,
    registry: PromptRegistry,
) -> bool:
    """Summarize ``messages`` (through ``through_seq``) and CAS-upsert it. Returns whether
    a summary was written (False on a best-effort skip or a stale CAS no-op)."""
    prompt = registry.render("summarize_session", transcript=_transcript(messages))
    ctx = GatewayContext(
        org_id=org_id, user_id=owner_user_id, feature=UsageFeature.CHAT, request_id="summary-job"
    )
    try:
        completion = await gateway.complete(prompt=prompt, ctx=ctx)
    except AppError as exc:
        # Best-effort: a summary is an optimization, never a reason to fail the session.
        _logger.warning(
            "conversation.summary.skipped", session_id=str(session_id), error=type(exc).__name__
        )
        return False
    return await summaries.upsert_if_newer(
        org_id, session_id, completion.text, through_seq, prompt.template_version
    )
