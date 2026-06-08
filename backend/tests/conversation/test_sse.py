"""SSE streaming matrix (ADR 0014): every terminal shape proven on the wire + the
framing/heartbeat/disconnect semantics. Event-order invariants:

    meta → token* → citations → done     (grounded answer; citations are the validated set)
    meta → token* → refusal               (validation failed AFTER streaming — supersede)
    meta → refusal                         (low confidence — refused BEFORE generation)
    meta → [token*] → error                (infrastructure failure — no assistant row)

Persistence is asserted to hold the cite-or-refuse invariant in storage, and an
infrastructure failure / disconnect must leave NO assistant row (re-askable).
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from uuid import uuid4

from src.ai.gateway import UpstreamUnavailableError
from src.conversation.router import _sse_body
from src.conversation.schemas import ConversationStreamEvent, MetaEvent, TokenEvent
from src.conversation.sse import HEARTBEAT_FRAME
from tests.conversation.conftest import (
    ChatHarness,
    auth_header,
    create_session,
    parse_sse,
    register_and_login,
)


async def _send(harness: ChatHarness, token: str, session_id: str, content: str) -> str:
    resp = await harness.client.post(
        f"/api/v1/chat/sessions/{session_id}/messages",
        headers=auth_header(token),
        json={"content": content},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    return resp.text


async def _messages(harness: ChatHarness, token: str, session_id: str) -> list[dict[str, object]]:
    resp = await harness.client.get(
        f"/api/v1/chat/sessions/{session_id}/messages", headers=auth_header(token)
    )
    body: list[dict[str, object]] = resp.json()["messages"]
    return body


async def test_grounded_answer_streams_tokens_then_citations_then_done(
    chat_harness: ChatHarness,
) -> None:
    token = await register_and_login(chat_harness.client, "acme")
    session_id = await create_session(chat_harness.client, token)
    chat_harness.retriever.set_sources("Paris is the capital of France.")
    chat_harness.gateway.queue_stream(["The capital ", "is Paris ", "[1]."])

    events = parse_sse(await _send(chat_harness, token, session_id, "What is the capital?"))
    names = [name for name, _ in events]
    assert names[0] == "meta"
    assert names[-2:] == ["citations", "done"]
    assert "token" in names
    assert "refusal" not in names and "error" not in names

    # The streamed text reassembles, and the terminal citations are the validated set.
    streamed = "".join(json.loads(d)["text"] for n, d in events if n == "token")
    assert streamed == "The capital is Paris [1]."
    citations = next(json.loads(d)["items"] for n, d in events if n == "citations")
    assert [c["marker_index"] for c in citations] == [1]

    # Persisted: a user row + a cited assistant row (no refusal_reason).
    stored = await _messages(chat_harness, token, session_id)
    assert [m["role"] for m in stored] == ["user", "assistant"]
    assistant = stored[1]
    assert assistant["refusal_reason"] is None
    citations_field = assistant["citations"]
    assert isinstance(citations_field, list) and len(citations_field) == 1


async def test_ungrounded_answer_streams_then_refusal_supersedes(
    chat_harness: ChatHarness,
) -> None:
    token = await register_and_login(chat_harness.client, "acme")
    session_id = await create_session(chat_harness.client, token)
    chat_harness.retriever.set_sources("Some source text.")
    # Tokens stream, but the answer carries no valid [n] marker → UNGROUNDED refusal.
    chat_harness.gateway.queue_stream(["I am ", "not sure."])

    events = parse_sse(await _send(chat_harness, token, session_id, "Tell me something."))
    names = [name for name, _ in events]
    assert names[0] == "meta"
    assert "token" in names  # tokens WERE streamed before the supersede
    assert names[-1] == "refusal"
    assert "citations" not in names and "done" not in names
    reason = json.loads(events[-1][1])["reason"]
    assert reason == "UNGROUNDED_ANSWER"

    # Persisted as a refusal row: refusal_reason set, no citations (cite-or-refuse).
    stored = await _messages(chat_harness, token, session_id)
    assistant = stored[1]
    assert assistant["refusal_reason"] == "UNGROUNDED_ANSWER"
    assert assistant["citations"] == []


async def test_low_confidence_refuses_before_generation_no_tokens(
    chat_harness: ChatHarness,
) -> None:
    token = await register_and_login(chat_harness.client, "acme")
    session_id = await create_session(chat_harness.client, token)
    chat_harness.retriever.set_sources()  # zero sources → refuse before generating

    events = parse_sse(await _send(chat_harness, token, session_id, "Anything?"))
    names = [name for name, _ in events]
    assert names == ["meta", "refusal"]
    assert json.loads(events[-1][1])["reason"] == "INSUFFICIENT_SOURCES"
    # No generation happened — zero spend, no stream call.
    assert chat_harness.gateway.stream_calls == []

    stored = await _messages(chat_harness, token, session_id)
    assert stored[1]["refusal_reason"] == "INSUFFICIENT_SOURCES"


async def test_infrastructure_failure_emits_error_and_persists_no_assistant_row(
    chat_harness: ChatHarness,
) -> None:
    token = await register_and_login(chat_harness.client, "acme")
    session_id = await create_session(chat_harness.client, token)
    chat_harness.retriever.set_sources("A source.")
    chat_harness.gateway.queue_stream([], error=UpstreamUnavailableError())

    events = parse_sse(await _send(chat_harness, token, session_id, "Hello?"))
    names = [name for name, _ in events]
    assert names[0] == "meta"
    assert names[-1] == "error"
    assert "refusal" not in names  # an outage is NOT a content refusal
    assert json.loads(events[-1][1])["code"] == "UPSTREAM_UNAVAILABLE"

    # No partial turn persisted — the turn is cleanly re-askable.
    stored = await _messages(chat_harness, token, session_id)
    assert stored == []


# --- framing unit tests (no HTTP): heartbeat + disconnect cancellation ---


async def test_sse_body_injects_heartbeat_during_idle_gap() -> None:
    async def slow_rest() -> AsyncGenerator[ConversationStreamEvent, None]:
        await asyncio.sleep(0.05)  # idle longer than the heartbeat interval
        yield TokenEvent(text="late")

    meta = MetaEvent(message_id=uuid4(), seq=1)
    frames = [frame async for frame in _sse_body(meta, slow_rest(), heartbeat_s=0.01)]
    assert frames[0].startswith("event: meta")
    assert HEARTBEAT_FRAME in frames
    assert any(f.startswith("event: token") for f in frames)


async def test_sse_body_disconnect_cancels_producer() -> None:
    closed = asyncio.Event()

    async def rest() -> AsyncGenerator[ConversationStreamEvent, None]:
        try:
            yield TokenEvent(text="first")
            await asyncio.sleep(10)  # would block forever if not cancelled
            yield TokenEvent(text="never")
        finally:
            closed.set()  # producer cleanup ran (disconnect propagated)

    body = _sse_body(MetaEvent(message_id=uuid4(), seq=1), rest(), heartbeat_s=5.0)
    assert (await body.__anext__()).startswith("event: meta")
    assert (await body.__anext__()).startswith("event: token")

    await body.aclose()  # simulate client disconnect
    await asyncio.wait_for(closed.wait(), timeout=1.0)
    assert closed.is_set()
