"""Conversation service — grounded-chat use cases (patterns.md §1/§5).

Session CRUD and feedback are ordinary 6-step methods. ``send_message_stream`` is the
streaming turn (ADR 0014): it authorizes + loads tenant/owner-scoped state, streams the
grounded pipeline's tokens live, and persists the turn ONLY once a terminal content
outcome (a validated answer or a typed refusal) is reached — so a client disconnect or an
infrastructure failure mid-stream leaves no partial assistant row and the turn is cleanly
re-askable. The user + assistant messages are written together (one unit of work); the
request session owns the commit.

Cite-or-refuse holds in storage, not just in flight: an assistant row is either citations
(validated markers only) or a refusal_reason — never both, never neither.
"""

from collections.abc import AsyncGenerator
from uuid import UUID, uuid4

import structlog

from src.ai.guardrails import Refusal
from src.conversation.context import HistoryTurn
from src.conversation.exceptions import MessageNotFoundError
from src.conversation.models import ChatMessage, ChatRole, ChatSession
from src.conversation.pipeline import (
    AnsweredTurn,
    GroundedAnswerPipeline,
    ResolvedCitation,
    TokenChunk,
)
from src.conversation.repository import (
    ChatMessageRepository,
    ChatSessionRepository,
    MessageFeedbackRepository,
    SessionSummaryRepository,
)
from src.conversation.rules import (
    decode_session_cursor,
    derive_title,
    encode_session_cursor,
    ensure_session_active,
    ensure_session_limit,
    ensure_session_owner,
    next_seq,
    validate_message_content,
)
from src.conversation.schemas import (
    Citation,
    CitationsEvent,
    ConversationStreamEvent,
    DoneEvent,
    FeedbackRequest,
    MessagePage,
    MessageResponse,
    MetaEvent,
    RefusalEvent,
    SessionPage,
    SessionResponse,
    TokenEvent,
)
from src.identity.schemas import AuthContext
from src.shared.audit import AuditEmitter
from src.shared.config import Settings

_logger = structlog.get_logger("conversation.service")


class ChatService:
    def __init__(
        self,
        *,
        sessions: ChatSessionRepository,
        messages: ChatMessageRepository,
        feedback: MessageFeedbackRepository,
        summaries: SessionSummaryRepository,
        pipeline: GroundedAnswerPipeline,
        audit: AuditEmitter,
        settings: Settings,
    ) -> None:
        self._sessions = sessions
        self._messages = messages
        self._feedback = feedback
        self._summaries = summaries
        self._pipeline = pipeline
        self._audit = audit
        self._settings = settings

    async def create_session(self, actor: AuthContext, title: str | None) -> SessionResponse:
        """Create a chat session for the actor. Time: O(1). Raises SESSION_LIMIT_EXCEEDED
        past the per-user cap."""
        count = await self._sessions.count_for_owner(actor.org_id, actor.user_id)
        ensure_session_limit(count, self._settings.chat_max_sessions_per_user)
        session = await self._sessions.create(
            ChatSession(org_id=actor.org_id, owner_user_id=actor.user_id, title=title)
        )
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="chat.session_created",
            resource_id=session.id,
        )
        return SessionResponse.from_model(session)

    async def list_sessions(
        self, actor: AuthContext, *, cursor: str | None, limit: int
    ) -> SessionPage:
        """One newest-first page of the actor's sessions. Time: O(limit). Raises
        INVALID_CURSOR on a malformed cursor."""
        capped = min(limit, self._settings.chat_session_page_size)
        before = decode_session_cursor(cursor) if cursor is not None else None
        rows = await self._sessions.list_for_owner(
            actor.org_id, actor.user_id, limit=capped + 1, before=before
        )
        has_more = len(rows) > capped
        page = rows[:capped]
        next_cursor = (
            encode_session_cursor(page[-1].last_message_at, page[-1].id) if has_more else None
        )
        return SessionPage(
            sessions=[SessionResponse.from_model(s) for s in page], next_cursor=next_cursor
        )

    async def update_session(
        self, actor: AuthContext, session_id: UUID, *, title: str | None, is_archived: bool | None
    ) -> SessionResponse:
        """Patch a session's title/archived flag (owner only). Time: O(1). Raises
        SESSION_NOT_FOUND if the actor does not own it."""
        session = ensure_session_owner(
            await self._sessions.get(actor.org_id, session_id), actor.user_id
        )
        await self._sessions.update_fields(
            actor.org_id, session_id, title=title, is_archived=is_archived
        )
        refreshed = await self._sessions.get(actor.org_id, session_id)
        assert refreshed is not None  # just updated under the same tenant scope
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="chat.session_updated",
            resource_id=session.id,
        )
        return SessionResponse.from_model(refreshed)

    async def delete_session(self, actor: AuthContext, session_id: UUID) -> None:
        """Hard-delete a session the actor owns (messages cascade). Time: O(m). Raises
        SESSION_NOT_FOUND if the actor does not own it."""
        ensure_session_owner(await self._sessions.get(actor.org_id, session_id), actor.user_id)
        await self._sessions.delete(actor.org_id, session_id)
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="chat.session_deleted",
            resource_id=session_id,
        )

    async def list_messages(
        self, actor: AuthContext, session_id: UUID, *, after_seq: int | None, limit: int
    ) -> MessagePage:
        """One ascending page of a session's messages, with the actor's own feedback
        attached (single batched read — no N+1). Time: O(limit). Raises SESSION_NOT_FOUND
        if the actor does not own the session."""
        ensure_session_owner(await self._sessions.get(actor.org_id, session_id), actor.user_id)
        capped = min(limit, self._settings.chat_message_page_size)
        rows = await self._messages.list_page(
            actor.org_id, session_id, after_seq=after_seq, limit=capped + 1
        )
        has_more = len(rows) > capped
        page = rows[:capped]
        feedback = await self._feedback.get_for_messages(
            actor.org_id, actor.user_id, [m.id for m in page]
        )
        next_cursor = page[-1].seq if has_more else None
        return MessagePage(
            messages=[MessageResponse.from_model(m, feedback.get(m.id)) for m in page],
            next_cursor=next_cursor,
        )

    async def submit_feedback(
        self, actor: AuthContext, message_id: UUID, body: FeedbackRequest
    ) -> None:
        """Record the actor's thumbs up/down on an assistant message they can see.
        Time: O(1). Raises MESSAGE_NOT_FOUND if the message is absent, not theirs (a
        session they don't own), or not an assistant message (no oracle)."""
        message = await self._messages.get(actor.org_id, message_id)
        if message is None or message.role is not ChatRole.ASSISTANT:
            raise MessageNotFoundError()
        ensure_session_owner(
            await self._sessions.get(actor.org_id, message.session_id), actor.user_id
        )
        await self._feedback.upsert(
            org_id=actor.org_id,
            message_id=message_id,
            user_id=actor.user_id,
            rating=body.rating,
            comment=body.comment,
        )
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="chat.feedback_submitted",
            resource_id=message_id,
            rating=body.rating.value,
        )

    async def send_message_stream(
        self, actor: AuthContext, session_id: UUID, content: str, *, request_id: str
    ) -> AsyncGenerator[ConversationStreamEvent, None]:
        """Stream one grounded turn (ADR 0014). AUTHORIZE + LOAD up front; stream tokens
        live; then validate-and-persist the turn atomically at the terminal outcome. A
        disconnect (the generator is closed) or an infrastructure AppError (which
        propagates) reaches NO persist call, so no partial row is written and the turn is
        re-askable. Time: O(history + sources + generated tokens). Raises SESSION_NOT_FOUND
        / SESSION_ARCHIVED / MESSAGE_* on bad input, and propagates gateway AppErrors."""
        session = ensure_session_owner(
            await self._sessions.get(actor.org_id, session_id), actor.user_id
        )
        ensure_session_active(session)
        normalized = validate_message_content(
            content, max_chars=self._settings.chat_message_max_chars
        )
        max_seq = await self._messages.max_seq(actor.org_id, session_id)
        user_seq = next_seq(max_seq)
        assistant_seq = user_seq + 1
        history = await self._load_history(actor, session_id, max_seq)
        summary_row = await self._summaries.get(actor.org_id, session_id)
        summary = summary_row.summary if summary_row is not None else None

        assistant_id = uuid4()
        yield MetaEvent(message_id=assistant_id, seq=assistant_seq)

        outcome: AnsweredTurn | Refusal | None = None
        parts: list[str] = []
        async for event in self._pipeline.answer_stream(
            actor=actor,
            raw_query=normalized,
            history=history,
            summary=summary,
            request_id=request_id,
        ):
            if isinstance(event, TokenChunk):
                parts.append(event.text)
                yield TokenEvent(text=event.text)
            else:
                outcome = event  # terminal AnsweredTurn | Refusal
        assert outcome is not None  # answer_stream always yields a terminal event last

        await self._persist_turn(
            actor,
            session,
            user_seq=user_seq,
            user_content=normalized,
            assistant_id=assistant_id,
            assistant_seq=assistant_seq,
            outcome=outcome,
        )

        if isinstance(outcome, AnsweredTurn):
            yield CitationsEvent(items=[_citation_schema(c) for c in outcome.citations])
            yield DoneEvent(
                message_id=assistant_id,
                prompt_version=outcome.prompt_version,
                trace_id=outcome.trace_id,
            )
        else:
            yield RefusalEvent(reason=outcome.reason)

    async def _load_history(
        self, actor: AuthContext, session_id: UUID, max_seq: int | None
    ) -> list[HistoryTurn]:
        """Prior turns as assembly inputs. Refusal rows (empty content) are excluded — a
        past refusal is not conversational context. Time: O(m)."""
        if max_seq is None:
            return []
        rows = await self._messages.list_through_seq(actor.org_id, session_id, max_seq)
        return [HistoryTurn(role=m.role.value, content=m.content) for m in rows if m.content]

    async def _persist_turn(
        self,
        actor: AuthContext,
        session: ChatSession,
        *,
        user_seq: int,
        user_content: str,
        assistant_id: UUID,
        assistant_seq: int,
        outcome: AnsweredTurn | Refusal,
    ) -> None:
        """Write the user message + the assistant message (cited or refusal) as one unit
        of work, bump session recency, and audit (message length only — never content).
        The request session commits this; nothing here is written on a failed/cancelled
        stream because the terminal is never reached. Time: O(citations)."""
        await self._messages.add(
            ChatMessage(
                org_id=actor.org_id,
                session_id=session.id,
                role=ChatRole.USER,
                content=user_content,
                seq=user_seq,
            )
        )
        if isinstance(outcome, AnsweredTurn):
            assistant = ChatMessage(
                id=assistant_id,
                org_id=actor.org_id,
                session_id=session.id,
                role=ChatRole.ASSISTANT,
                content=outcome.content,
                citations=[_citation_json(c) for c in outcome.citations],
                prompt_version=outcome.prompt_version,
                model_used=outcome.model_used,
                trace_id=outcome.trace_id,
                seq=assistant_seq,
            )
        else:
            assistant = ChatMessage(
                id=assistant_id,
                org_id=actor.org_id,
                session_id=session.id,
                role=ChatRole.ASSISTANT,
                content="",  # a refusal carries no answer text — only refusal_reason
                refusal_reason=outcome.reason,
                seq=assistant_seq,
            )
        await self._messages.add(assistant)

        # First turn names the session from the user's question, if still untitled.
        new_title = derive_title(user_content) if session.title is None else None
        await self._sessions.update_fields(
            actor.org_id, session.id, title=new_title, is_archived=None
        )
        await self._sessions.touch_last_message_at(actor.org_id, session.id)
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="chat.message_sent",
            resource_id=session.id,
            content_length=len(user_content),  # length only — never the message text
            outcome="answered" if isinstance(outcome, AnsweredTurn) else "refused",
        )


def _citation_schema(citation: ResolvedCitation) -> Citation:
    return Citation(
        marker_index=citation.marker_index,
        chunk_id=citation.chunk_id,
        document_id=citation.document_id,
        filename=citation.filename,
        page_ref=citation.page_ref,
    )


def _citation_json(citation: ResolvedCitation) -> dict[str, object]:
    """Storage shape for the JSONB citations column (UUIDs as strings)."""
    return {
        "marker_index": citation.marker_index,
        "chunk_id": str(citation.chunk_id),
        "document_id": str(citation.document_id),
        "filename": citation.filename,
        "page_ref": citation.page_ref,
    }
