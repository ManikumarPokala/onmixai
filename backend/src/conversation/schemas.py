"""Conversation request/response + SSE wire schemas (allow-lists; sensitive fields
structurally absent — CLAUDE.md §10). The SSE event payloads are defined here so the
frontend's generated client (Task 7) has typed models for the stream protocol (Task 6).
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from src.conversation.models import ChatRole, FeedbackRating

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
