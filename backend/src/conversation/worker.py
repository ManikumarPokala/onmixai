"""ARQ task: refresh a session's rolling summary (Task 3). Dependencies come from the
worker ``ctx`` (wired at the composition root, src/worker.py) — this module never imports
a provider adapter, keeping the gateway behind its Protocol (CLAUDE.md §3.6)."""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.ai.gateway import LLMGateway
from src.ai.prompt_registry import get_prompt_registry
from src.conversation.repository import (
    ChatMessageRepository,
    ChatSessionRepository,
    SessionSummaryRepository,
)
from src.conversation.summary import update_session_summary
from src.shared.database import set_tenant_context


async def summarize_session(ctx: dict[str, Any], org_id: str, session_id: str) -> None:
    """Summarize the session through its current max seq and CAS-upsert it. Idempotent
    and best-effort (see summary.py); a missing/empty session is a no-op."""
    maker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    gateway_factory: Callable[[AsyncSession], LLMGateway] = ctx["gateway_factory"]
    oid, sid = UUID(org_id), UUID(session_id)
    async with maker() as session:
        await set_tenant_context(session, oid)
        chat_session = await ChatSessionRepository(session).get(oid, sid)
        if chat_session is None:
            return
        messages = ChatMessageRepository(session)
        through_seq = await messages.max_seq(oid, sid)
        if through_seq is None:
            return
        await update_session_summary(
            org_id=oid,
            owner_user_id=chat_session.owner_user_id,
            session_id=sid,
            through_seq=through_seq,
            messages=await messages.list_through_seq(oid, sid, through_seq),
            gateway=gateway_factory(session),
            summaries=SessionSummaryRepository(session),
            registry=get_prompt_registry(),
        )
        await session.commit()
