"""Conversation domain queries. All reads are tenant-scoped (RLS + org_id predicate);
the per-user ownership rule is applied in rules.py over what these return. No business
decisions here (CLAUDE.md §3.1). Every list is bounded by a caller-supplied ``limit``;
there is no unbounded SELECT."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, literal, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.conversation.models import (
    ChatMessage,
    ChatSession,
    FeedbackRating,
    MessageFeedback,
    SessionSummary,
)


class ChatSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, session: ChatSession) -> ChatSession:
        """Persist a new session (id/timestamps from defaults). Time: O(1)."""
        self._session.add(session)
        await self._session.flush()
        return session

    async def get(self, org_id: UUID, session_id: UUID) -> ChatSession | None:
        result = await self._session.execute(
            select(ChatSession).where(ChatSession.org_id == org_id, ChatSession.id == session_id)
        )
        return result.scalar_one_or_none()

    async def list_for_owner(
        self,
        org_id: UUID,
        owner_user_id: UUID,
        *,
        limit: int,
        before: tuple[datetime, UUID] | None,
    ) -> list[ChatSession]:
        """One page of the owner's sessions, newest first, via a (last_message_at, id)
        keyset cursor. Returns up to ``limit`` rows. Time: O(limit) on the
        ix_chat_sessions_org_owner_last index."""
        stmt = select(ChatSession).where(
            ChatSession.org_id == org_id, ChatSession.owner_user_id == owner_user_id
        )
        if before is not None:
            stmt = stmt.where(
                tuple_(ChatSession.last_message_at, ChatSession.id)
                < tuple_(literal(before[0]), literal(before[1]))
            )
        stmt = stmt.order_by(ChatSession.last_message_at.desc(), ChatSession.id.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_owner(self, org_id: UUID, owner_user_id: UUID) -> int:
        """Number of sessions owned by the user (for the per-user cap). Time: O(1)."""
        result = await self._session.execute(
            select(func.count())
            .select_from(ChatSession)
            .where(ChatSession.org_id == org_id, ChatSession.owner_user_id == owner_user_id)
        )
        return result.scalar_one()

    async def update_fields(
        self,
        org_id: UUID,
        session_id: UUID,
        *,
        title: str | None,
        is_archived: bool | None,
    ) -> None:
        """Patch a session's title/archived flag (only the provided fields). The caller
        has already verified ownership. Time: O(1)."""
        values: dict[str, Any] = {}
        if title is not None:
            values["title"] = title
        if is_archived is not None:
            values["is_archived"] = is_archived
        if not values:
            return
        await self._session.execute(
            update(ChatSession)
            .where(ChatSession.org_id == org_id, ChatSession.id == session_id)
            .values(**values)
        )

    async def delete(self, org_id: UUID, session_id: UUID) -> None:
        """Hard-delete a session; messages/feedback/summary cascade. Time: O(m)."""
        await self._session.execute(
            delete(ChatSession).where(ChatSession.org_id == org_id, ChatSession.id == session_id)
        )

    async def touch_last_message_at(self, org_id: UUID, session_id: UUID) -> None:
        """Bump a session's recency to now() after a new turn lands. Time: O(1)."""
        await self._session.execute(
            update(ChatSession)
            .where(ChatSession.org_id == org_id, ChatSession.id == session_id)
            .values(last_message_at=func.now())
        )


class ChatMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, message: ChatMessage) -> ChatMessage:
        """Persist one message. The (session_id, seq) unique constraint makes a duplicate
        seq a hard failure rather than silent corruption. Time: O(1)."""
        self._session.add(message)
        await self._session.flush()
        return message

    async def get(self, org_id: UUID, message_id: UUID) -> ChatMessage | None:
        result = await self._session.execute(
            select(ChatMessage).where(ChatMessage.org_id == org_id, ChatMessage.id == message_id)
        )
        return result.scalar_one_or_none()

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

    async def list_page(
        self, org_id: UUID, session_id: UUID, *, after_seq: int | None, limit: int
    ) -> list[ChatMessage]:
        """One ascending page of a session's messages with seq > ``after_seq``. Returns up
        to ``limit`` rows. Time: O(limit) on ix_chat_messages_org_session_seq."""
        stmt = select(ChatMessage).where(
            ChatMessage.org_id == org_id, ChatMessage.session_id == session_id
        )
        if after_seq is not None:
            stmt = stmt.where(ChatMessage.seq > after_seq)
        stmt = stmt.order_by(ChatMessage.seq).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class MessageFeedbackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        *,
        org_id: UUID,
        message_id: UUID,
        user_id: UUID,
        rating: FeedbackRating,
        comment: str | None,
    ) -> None:
        """Set (or replace) this user's feedback on a message — idempotent per
        (message_id, user_id). Time: O(1)."""
        stmt = (
            pg_insert(MessageFeedback)
            .values(
                org_id=org_id,
                message_id=message_id,
                user_id=user_id,
                rating=rating,
                comment=comment,
            )
            .on_conflict_do_update(
                constraint="uq_message_feedback_message_id_user_id",
                set_={"rating": rating, "comment": comment},
            )
        )
        await self._session.execute(stmt)

    async def get_for_messages(
        self, org_id: UUID, user_id: UUID, message_ids: list[UUID]
    ) -> dict[UUID, MessageFeedback]:
        """This user's feedback for a batch of messages, keyed by message_id (avoids an
        N+1 when shaping a message page). Time: O(k) over the k requested ids."""
        if not message_ids:
            return {}
        result = await self._session.execute(
            select(MessageFeedback).where(
                MessageFeedback.org_id == org_id,
                MessageFeedback.user_id == user_id,
                MessageFeedback.message_id.in_(message_ids),
            )
        )
        return {fb.message_id: fb for fb in result.scalars().all()}


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
