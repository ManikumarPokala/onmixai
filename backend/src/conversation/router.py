"""Conversation HTTP routes — thin: validate, one service call, shape response.

The send-message route is the exception to "return a DTO": it returns an SSE stream
(ADR 0014). Token events stream live; a terminal ``citations``+``done`` or ``refusal``
event closes a normal turn; an infrastructure ``AppError`` raised mid-stream is framed as
a terminal ``error`` event (no assistant row persisted) rather than a JSON 500 — the
response status is already 200 by the time tokens flow. A heartbeat comment keeps idle
connections alive; a client disconnect cancels generation (no partial persist).
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import suppress
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from src.conversation.dependencies import get_chat_service, set_user_scoped_key
from src.conversation.schemas import (
    ConversationStreamEvent,
    CreateSessionRequest,
    ErrorEvent,
    FeedbackRequest,
    MessagePage,
    SendMessageRequest,
    SessionPage,
    SessionResponse,
    UpdateSessionRequest,
)
from src.conversation.service import ChatService
from src.conversation.sse import HEARTBEAT_FRAME, format_event
from src.identity.dependencies import get_current_user
from src.identity.schemas import AuthContext
from src.shared.config import Settings, get_settings
from src.shared.errors import AppError
from src.shared.ratelimit import CHAT_RATE_LIMIT, limiter

router = APIRouter()

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.post("/chat/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateSessionRequest,
    actor: AuthContext = Depends(get_current_user),
    service: ChatService = Depends(get_chat_service),
) -> SessionResponse:
    return await service.create_session(actor, body.title)


@router.get("/chat/sessions")
async def list_sessions(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    actor: AuthContext = Depends(get_current_user),
    service: ChatService = Depends(get_chat_service),
) -> SessionPage:
    return await service.list_sessions(actor, cursor=cursor, limit=limit)


@router.patch("/chat/sessions/{session_id}")
async def update_session(
    session_id: UUID,
    body: UpdateSessionRequest,
    actor: AuthContext = Depends(get_current_user),
    service: ChatService = Depends(get_chat_service),
) -> SessionResponse:
    return await service.update_session(
        actor, session_id, title=body.title, is_archived=body.is_archived
    )


@router.delete("/chat/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: UUID,
    actor: AuthContext = Depends(get_current_user),
    service: ChatService = Depends(get_chat_service),
) -> None:
    await service.delete_session(actor, session_id)


@router.get("/chat/sessions/{session_id}/messages")
async def list_messages(
    session_id: UUID,
    after_seq: int | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    actor: AuthContext = Depends(get_current_user),
    service: ChatService = Depends(get_chat_service),
) -> MessagePage:
    return await service.list_messages(actor, session_id, after_seq=after_seq, limit=limit)


@router.post(
    "/chat/sessions/{session_id}/messages",
    dependencies=[Depends(set_user_scoped_key)],
)
@limiter.limit(CHAT_RATE_LIMIT)
async def send_message(
    request: Request,
    session_id: UUID,
    body: SendMessageRequest,
    actor: AuthContext = Depends(get_current_user),
    service: ChatService = Depends(get_chat_service),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    request_id: str = request.state.request_id
    events = service.send_message_stream(actor, session_id, body.content, request_id=request_id)
    # Prime the generator so AUTHORIZE + LOAD + input validation run BEFORE the response is
    # constructed: a caller error (archived session, bad input, not owner) surfaces here as a
    # normal JSON 4xx, not as a 200 stream. The first event (`meta`) is then re-emitted by
    # the body. An infrastructure failure happens later (mid-generation) and is framed as a
    # terminal `error` event instead — the status is already 200 by then.
    first = await events.__anext__()
    body_iter = _sse_body(first, events, heartbeat_s=settings.chat_stream_heartbeat_seconds)
    return StreamingResponse(body_iter, media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/chat/messages/{message_id}/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def submit_feedback(
    message_id: UUID,
    body: FeedbackRequest,
    actor: AuthContext = Depends(get_current_user),
    service: ChatService = Depends(get_chat_service),
) -> Response:
    await service.submit_feedback(actor, message_id, body)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


_DONE = object()  # producer-finished sentinel on the event queue


async def _sse_body(
    first: ConversationStreamEvent,
    events: AsyncGenerator[ConversationStreamEvent, None],
    *,
    heartbeat_s: float,
) -> AsyncGenerator[str, None]:
    """Frame the service's event stream as SSE, injecting a heartbeat during idle gaps and
    framing a terminal infrastructure ``AppError`` as an ``error`` event. ``first`` is the
    already-pulled ``meta`` event (priming surfaced any caller error as a 4xx); ``events``
    is the rest of the same generator.

    The remainder runs as its own task feeding a queue; the consumer races the queue against
    the heartbeat interval. A heartbeat timeout cancels only ``queue.get`` (never a generator
    step, which would corrupt it). On client disconnect the body generator is cancelled, the
    ``finally`` cancels the producer and closes ``events`` — so generation stops in the
    pipeline and no partial turn is persisted.
    """
    yield format_event(first)
    queue: asyncio.Queue[object] = asyncio.Queue()
    failure: list[AppError] = []

    async def produce() -> None:
        try:
            async for event in events:
                await queue.put(event)
        except AppError as exc:
            failure.append(exc)
        finally:
            await queue.put(_DONE)

    task = asyncio.create_task(produce())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
            except TimeoutError:
                yield HEARTBEAT_FRAME
                continue
            if item is _DONE:
                break
            assert isinstance(item, ConversationStreamEvent)  # only events or _DONE enqueued
            yield format_event(item)
        if failure:
            yield format_event(ErrorEvent(code=failure[0].code))
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await events.aclose()  # belt-and-suspenders: close the source on disconnect
