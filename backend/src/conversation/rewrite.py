"""Best-effort follow-up query rewriting. Resolves anaphora in the latest message into
a standalone retrieval query via the gateway — but NEVER blocks a chat turn: on any
gateway failure (timeout, provider down, budget block) it falls back to the raw query,
recording a typed fallback rather than raising. The first message in a session skips
rewriting (nothing to resolve). The rewritten text is used for RETRIEVAL ONLY; the
user's original message is what gets stored and shown."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import structlog

from src.ai.gateway import GatewayContext, LLMGateway
from src.ai.models import UsageFeature
from src.ai.prompt_registry import PromptRegistry
from src.conversation.context import HistoryTurn
from src.shared.errors import AppError

_logger = structlog.get_logger("conversation.rewrite")

RewriteSource = Literal["rewritten", "raw_first_message", "raw_fallback"]


@dataclass(frozen=True, slots=True)
class RewrittenQuery:
    query: str  # what goes to retrieval (rewritten or the raw fallback)
    source: RewriteSource  # for the trace: was the rewrite used or fell back


def _history_text(history_tail: list[HistoryTurn]) -> str:
    return "\n".join(f"{turn.role}: {turn.content}" for turn in history_tail)


async def rewrite_query(
    history_tail: list[HistoryTurn],
    raw_query: str,
    *,
    gateway: LLMGateway,
    registry: PromptRegistry,
    org_id: UUID,
    user_id: UUID,
    request_id: str,
    max_chars: int,
) -> RewrittenQuery:
    """Return the query to retrieve with. Time: one gateway call (skipped on the first
    message). Never raises on a gateway failure — falls back to ``raw_query``."""
    if not history_tail:
        return RewrittenQuery(raw_query, "raw_first_message")  # nothing to resolve

    prompt = registry.render(
        "rewrite_query", history=_history_text(history_tail), question=raw_query
    )
    ctx = GatewayContext(
        org_id=org_id, user_id=user_id, feature=UsageFeature.CHAT, request_id=request_id
    )
    try:
        completion = await gateway.complete(prompt=prompt, ctx=ctx)
    except AppError as exc:
        _logger.info("conversation.rewrite.fallback", error=type(exc).__name__)
        return RewrittenQuery(raw_query, "raw_fallback")

    # Sanitize before it ever reaches retrieval: collapse whitespace, cap length.
    rewritten = " ".join(completion.text.split())[:max_chars].strip()
    if not rewritten:
        return RewrittenQuery(raw_query, "raw_fallback")  # empty rewrite → raw
    return RewrittenQuery(rewritten, "rewritten")
