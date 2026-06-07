"""Conversation domain queries. All reads are tenant-scoped (RLS + org_id predicate);
the per-user ownership rule is applied in rules.py over what these return. No business
decisions here (CLAUDE.md §3.1)."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.conversation.models import ChatMessage, ChatSession, SessionSummary


class ChatSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, org_id: UUID, session_id: UUID) -> ChatSession | None:
        result = await self._session.execute(
            select(ChatSession).where(ChatSession.org_id == org_id, ChatSession.id == session_id)
        )
        return result.scalar_one_or_none()


class ChatMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def max_seq(self, org_id: UUID, session_id: UUID) -> int | None:
        """Highest message seq in the session, or None if empty. Time: O(1) (index)."""
        result = await self._session.execute(
            select(func.max(ChatMessage.seq)).where(
                ChatMessage.org_id == org_id, ChatMessage.session_id == session_id
            )
        )
        return result.scalar_one_or_none()

    async def list_through_seq(
        self, org_id: UUID, session_id: UUID, through_seq: int
    ) -> list[ChatMessage]:
        """Messages with seq ≤ through_seq, in order. Time: O(m) over the session."""
        result = await self._session.execute(
            select(ChatMessage)
            .where(
                ChatMessage.org_id == org_id,
                ChatMessage.session_id == session_id,
                ChatMessage.seq <= through_seq,
            )
            .order_by(ChatMessage.seq)
        )
        return list(result.scalars().all())


class SessionSummaryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, org_id: UUID, session_id: UUID) -> SessionSummary | None:
        result = await self._session.execute(
            select(SessionSummary).where(
                SessionSummary.org_id == org_id, SessionSummary.session_id == session_id
            )
        )
        return result.scalar_one_or_none()

    async def upsert_if_newer(
        self,
        org_id: UUID,
        session_id: UUID,
        summary: str,
        through_seq: int,
        prompt_version: str | None,
    ) -> bool:
        """Insert the summary, or update it only if ``through_seq`` advances (compare-and-
        set) — so a slow/out-of-order summary job can never overwrite a fresher summary.
        Returns whether it wrote. Time: O(1)."""
        stmt = (
            pg_insert(SessionSummary)
            .values(
                org_id=org_id,
                session_id=session_id,
                summary=summary,
                through_seq=through_seq,
                prompt_version=prompt_version,
            )
            .on_conflict_do_update(
                constraint="uq_session_summaries_session_id",
                set_={
                    "summary": summary,
                    "through_seq": through_seq,
                    "prompt_version": prompt_version,
                    "updated_at": func.now(),
                },
                where=SessionSummary.through_seq < through_seq,
            )
            .returning(SessionSummary.id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None
