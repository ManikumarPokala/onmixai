"""Conversation domain ORM models: chat sessions, messages, feedback, and rolling
session summaries.

All four tables are tenant-owned (``org_id NOT NULL``) and protected by forced
Row-Level Security created in migration 0007 (CLAUDE.md §4). Sessions add a SECOND
isolation axis on top of the org: they are private to their ``owner_user_id`` even
within a tenant (enforced in rules.py / repository, Task 2). Every assistant message is
either cited (``citations``, validated markers only) or a typed refusal
(``refusal_reason``) — there is no third state.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.shared.database import Base

CHAT_ROLE_ENUM_NAME = "chat_role"
FEEDBACK_RATING_ENUM_NAME = "feedback_rating"


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class FeedbackRating(StrEnum):
    UP = "up"
    DOWN = "down"


def _chat_role() -> Enum:
    return Enum(ChatRole, name=CHAT_ROLE_ENUM_NAME, values_callable=lambda e: [m.value for m in e])


def _feedback_rating() -> Enum:
    return Enum(
        FeedbackRating,
        name=FEEDBACK_RATING_ENUM_NAME,
        values_callable=lambda e: [m.value for m in e],
    )


class ChatSession(Base):
    """A user's private conversation thread within a tenant."""

    __tablename__ = "chat_sessions"
    __table_args__ = (
        # Serves the owner's session list ordered by recency (last_message_at DESC); a
        # plain b-tree is scanned backwards for the DESC order.
        Index(
            "ix_chat_sessions_org_owner_last",
            "org_id",
            "owner_user_id",
            "last_message_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ChatMessage(Base):
    """One turn. An assistant message is either cited or a refusal — never both, never
    neither (the cite-or-refuse invariant, enforced by the pipeline)."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_chat_messages_session_id_seq"),
        Index("ix_chat_messages_org_session_seq", "org_id", "session_id", "seq"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"))
    role: Mapped[ChatRole] = mapped_column(_chat_role())
    content: Mapped[str] = mapped_column(Text)
    # Validated citations only: [{chunk_id, document_id, marker_index, page_ref}]. Empty
    # for user messages and refusals.
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    refusal_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seq: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MessageFeedback(Base):
    """A user's thumbs up/down (+ optional comment) on an assistant message."""

    __tablename__ = "message_feedback"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_message_feedback_message_id_user_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    message_id: Mapped[UUID] = mapped_column(ForeignKey("chat_messages.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    rating: Mapped[FeedbackRating] = mapped_column(_feedback_rating())
    comment: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SessionSummary(Base):
    """A rolling summary of a session through ``through_seq`` (Task 3), one per session."""

    __tablename__ = "session_summaries"
    __table_args__ = (UniqueConstraint("session_id", name="uq_session_summaries_session_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"))
    summary: Mapped[str] = mapped_column(Text)
    through_seq: Mapped[int] = mapped_column(Integer)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
