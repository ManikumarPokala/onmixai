"""Conversation request/response + SSE wire schemas (allow-lists; sensitive fields
structurally absent — CLAUDE.md §10). The SSE event payloads are defined here so the
frontend's generated client (Task 7) has typed models for the stream protocol (Task 6).
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from src.conversation.models import (
    ChatMessage,
    ChatRole,
    ChatSession,
    FeedbackRating,
    GoldenCandidate,
    GoldenCandidateStatus,
    MessageFeedback,
)

# --- requests ---


class CreateSessionRequest(BaseModel):
    title: str | None = None


class UpdateSessionRequest(BaseModel):
    title: str | None = None
    is_archived: bool | None = None


class SendMessageRequest(BaseModel):
    content: str


class FeedbackRequest(BaseModel):
    rating: FeedbackRating
    comment: str | None = Field(default=None, max_length=2000)


# --- responses ---


class SessionResponse(BaseModel):
    id: UUID
    title: str | None
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime

    @classmethod
    def from_model(cls, session: ChatSession) -> "SessionResponse":
        return cls(
            id=session.id,
            title=session.title,
            is_archived=session.is_archived,
            created_at=session.created_at,
            updated_at=session.updated_at,
            last_message_at=session.last_message_at,
        )


class SessionPage(BaseModel):
    sessions: list[SessionResponse]
    next_cursor: str | None


class Citation(BaseModel):
    """A validated inline citation, resolved to its source for the UI."""

    marker_index: int
    chunk_id: UUID
    document_id: UUID
    filename: str
    page_ref: int | None = None


class FeedbackState(BaseModel):
    rating: FeedbackRating | None
    comment: str | None = None


class MessageResponse(BaseModel):
    id: UUID
    seq: int
    role: ChatRole
    content: str
    citations: list[Citation]
    refusal_reason: str | None
    prompt_version: str | None
    model_used: str | None
    created_at: datetime
    feedback: FeedbackState | None = None

    @classmethod
    def from_model(
        cls, message: ChatMessage, feedback: MessageFeedback | None
    ) -> "MessageResponse":
        return cls(
            id=message.id,
            seq=message.seq,
            role=message.role,
            content=message.content,
            citations=[Citation(**c) for c in message.citations],
            refusal_reason=message.refusal_reason,
            prompt_version=message.prompt_version,
            model_used=message.model_used,
            created_at=message.created_at,
            feedback=(
                FeedbackState(rating=feedback.rating, comment=feedback.comment)
                if feedback is not None
                else None
            ),
        )


class MessagePage(BaseModel):
    messages: list[MessageResponse]
    next_cursor: int | None


# --- SSE stream events (wire protocol, Task 6 / ADR 0014) ---
# Each event has a literal ``event`` discriminator so the client can switch on it. A
# terminal ``refusal`` event supersedes any streamed ``token`` text.


class MetaEvent(BaseModel):
    event: Literal["meta"] = "meta"
    message_id: UUID
    seq: int


class TokenEvent(BaseModel):
    event: Literal["token"] = "token"
    text: str


class CitationsEvent(BaseModel):
    event: Literal["citations"] = "citations"
    items: list[Citation]


class DoneEvent(BaseModel):
    event: Literal["done"] = "done"
    message_id: UUID
    prompt_version: str | None
    trace_id: str | None


class RefusalEvent(BaseModel):
    event: Literal["refusal"] = "refusal"
    reason: str  # supersedes streamed tokens — the client replaces content (ADR 0014)


class ErrorEvent(BaseModel):
    event: Literal["error"] = "error"
    code: str  # typed envelope code only — never internals


# What the service yields while streaming a turn (ADR 0014). ``ErrorEvent`` is NOT here:
# an infrastructure failure propagates as an exception and the router frames it as the
# terminal `error` event — the service never fabricates one.
ConversationStreamEvent = MetaEvent | TokenEvent | CitationsEvent | DoneEvent | RefusalEvent


class ReviewItem(BaseModel):
    """One UP-rated Q&A surfaced for golden curation. Content is PII-REDACTED before it reaches a
    reviewer; only redaction counts (never the matched values) accompany it."""

    message_id: UUID
    question: str
    answer: str
    comment: str | None
    redaction_counts: dict[str, int]
    created_at: datetime


class ReviewPage(BaseModel):
    items: list[ReviewItem]
    next_cursor: str | None


class GoldenCandidateResponse(BaseModel):
    """A curated, PII-redacted golden candidate. Approval here never writes the eval golden set."""

    id: UUID
    source_message_id: UUID | None
    question: str
    answer: str
    rating: FeedbackRating
    status: GoldenCandidateStatus
    redaction_counts: dict[str, int]
    created_at: datetime
    decided_at: datetime | None

    @classmethod
    def from_model(cls, c: "GoldenCandidate") -> "GoldenCandidateResponse":
        return cls(
            id=c.id,
            source_message_id=c.source_message_id,
            question=c.question,
            answer=c.answer,
            rating=c.rating,
            status=c.status,
            redaction_counts=dict(c.redaction_counts),
            created_at=c.created_at,
            decided_at=c.decided_at,
        )


class GoldenCandidatePage(BaseModel):
    candidates: list[GoldenCandidateResponse]
    next_cursor: str | None


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
