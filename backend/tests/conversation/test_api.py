"""Chat session/message/feedback HTTP API: CRUD, pagination, ownership isolation (the
per-user second axis AND cross-org), feedback, and the per-user rate limit. The SSE
send path's event-order matrix lives in test_sse.py."""

from uuid import UUID, uuid4

from src.identity.models import Role, User
from src.shared.database import set_tenant_context
from src.shared.security import create_access_token, hash_password
from tests.conversation.conftest import (
    ChatHarness,
    auth_header,
    create_session,
    register_and_login,
)


async def _second_user_token(harness: ChatHarness, owner_token: str) -> tuple[str, UUID]:
    """Mint a token for a SECOND user in the SAME org as ``owner_token`` (no invite
    endpoint exists yet) — to exercise the per-user isolation axis within one tenant."""
    me = (await harness.client.get("/api/v1/users/me", headers=auth_header(owner_token))).json()
    org_id = UUID(me["org_id"])
    user_id = uuid4()
    await set_tenant_context(harness.db_session, org_id)
    harness.db_session.add(
        User(
            id=user_id,
            org_id=org_id,
            email=f"second-{user_id.hex[:8]}@a.test",
            password_hash=hash_password("password-123456"),
            full_name="Second",
            role=Role.MEMBER,
        )
    )
    await harness.db_session.flush()
    token = create_access_token(
        settings=harness.settings, user_id=user_id, org_id=org_id, role=Role.MEMBER.value
    )
    return token, user_id


async def test_create_and_list_sessions(chat_harness: ChatHarness) -> None:
    token = await register_and_login(chat_harness.client, "acme")
    created = await chat_harness.client.post(
        "/api/v1/chat/sessions", headers=auth_header(token), json={"title": "Planning"}
    )
    assert created.status_code == 201
    assert created.json()["title"] == "Planning"

    listed = await chat_harness.client.get("/api/v1/chat/sessions", headers=auth_header(token))
    assert listed.status_code == 200
    body = listed.json()
    assert [s["title"] for s in body["sessions"]] == ["Planning"]
    assert body["next_cursor"] is None


async def test_session_list_is_paginated_and_capped(chat_harness: ChatHarness) -> None:
    token = await register_and_login(chat_harness.client, "acme")
    for _ in range(3):
        await create_session(chat_harness.client, token)

    page = await chat_harness.client.get(
        "/api/v1/chat/sessions?limit=2", headers=auth_header(token)
    )
    body = page.json()
    assert len(body["sessions"]) == 2
    assert body["next_cursor"] is not None

    nxt = await chat_harness.client.get(
        f"/api/v1/chat/sessions?limit=2&cursor={body['next_cursor']}",
        headers=auth_header(token),
    )
    nxt_body = nxt.json()
    assert len(nxt_body["sessions"]) == 1
    assert nxt_body["next_cursor"] is None


async def test_malformed_cursor_is_422(chat_harness: ChatHarness) -> None:
    token = await register_and_login(chat_harness.client, "acme")
    resp = await chat_harness.client.get(
        "/api/v1/chat/sessions?cursor=not-a-valid-cursor", headers=auth_header(token)
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_CURSOR"


async def test_update_and_delete_session(chat_harness: ChatHarness) -> None:
    token = await register_and_login(chat_harness.client, "acme")
    session_id = await create_session(chat_harness.client, token)

    patched = await chat_harness.client.patch(
        f"/api/v1/chat/sessions/{session_id}",
        headers=auth_header(token),
        json={"title": "Renamed", "is_archived": True},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Renamed"
    assert patched.json()["is_archived"] is True

    deleted = await chat_harness.client.delete(
        f"/api/v1/chat/sessions/{session_id}", headers=auth_header(token)
    )
    assert deleted.status_code == 204

    after = await chat_harness.client.get(
        f"/api/v1/chat/sessions/{session_id}/messages", headers=auth_header(token)
    )
    assert after.status_code == 404


async def test_another_user_in_same_org_cannot_access_session(chat_harness: ChatHarness) -> None:
    owner = await register_and_login(chat_harness.client, "acme")
    session_id = await create_session(chat_harness.client, owner)
    other_token, _ = await _second_user_token(chat_harness, owner)

    # Same tenant, different owner → indistinguishable from missing (404, no oracle).
    for method, url in [
        ("GET", f"/api/v1/chat/sessions/{session_id}/messages"),
        ("PATCH", f"/api/v1/chat/sessions/{session_id}"),
        ("DELETE", f"/api/v1/chat/sessions/{session_id}"),
    ]:
        resp = await chat_harness.client.request(
            method, url, headers=auth_header(other_token), json={}
        )
        assert resp.status_code == 404, (method, url)

    # The other user's session list does not include the owner's session.
    listed = await chat_harness.client.get(
        "/api/v1/chat/sessions", headers=auth_header(other_token)
    )
    assert listed.json()["sessions"] == []


async def test_cross_org_session_is_not_found(chat_harness: ChatHarness) -> None:
    a_token = await register_and_login(chat_harness.client, "orga")
    session_id = await create_session(chat_harness.client, a_token)
    b_token = await register_and_login(chat_harness.client, "orgb")

    resp = await chat_harness.client.delete(
        f"/api/v1/chat/sessions/{session_id}", headers=auth_header(b_token)
    )
    assert resp.status_code == 404


async def _send_grounded_turn(chat_harness: ChatHarness, token: str, session_id: str) -> None:
    chat_harness.retriever.set_sources("A grounding source.")
    chat_harness.gateway.queue_stream(["Answer ", "[1]."])
    resp = await chat_harness.client.post(
        f"/api/v1/chat/sessions/{session_id}/messages",
        headers=auth_header(token),
        json={"content": "question?"},
    )
    assert resp.status_code == 200
    resp.read()  # drain the SSE stream so the turn is persisted


async def test_feedback_round_trips_into_message_list(chat_harness: ChatHarness) -> None:
    token = await register_and_login(chat_harness.client, "acme")
    session_id = await create_session(chat_harness.client, token)
    await _send_grounded_turn(chat_harness, token, session_id)

    messages = (
        await chat_harness.client.get(
            f"/api/v1/chat/sessions/{session_id}/messages", headers=auth_header(token)
        )
    ).json()["messages"]
    assistant = next(m for m in messages if m["role"] == "assistant")
    assert assistant["feedback"] is None

    fb = await chat_harness.client.post(
        f"/api/v1/chat/messages/{assistant['id']}/feedback",
        headers=auth_header(token),
        json={"rating": "up", "comment": "clear"},
    )
    assert fb.status_code == 204

    refreshed = (
        await chat_harness.client.get(
            f"/api/v1/chat/sessions/{session_id}/messages", headers=auth_header(token)
        )
    ).json()["messages"]
    assistant = next(m for m in refreshed if m["role"] == "assistant")
    assert assistant["feedback"] == {"rating": "up", "comment": "clear"}


async def test_cannot_give_feedback_on_a_user_message(chat_harness: ChatHarness) -> None:
    token = await register_and_login(chat_harness.client, "acme")
    session_id = await create_session(chat_harness.client, token)
    await _send_grounded_turn(chat_harness, token, session_id)
    messages = (
        await chat_harness.client.get(
            f"/api/v1/chat/sessions/{session_id}/messages", headers=auth_header(token)
        )
    ).json()["messages"]
    user_msg = next(m for m in messages if m["role"] == "user")

    resp = await chat_harness.client.post(
        f"/api/v1/chat/messages/{user_msg['id']}/feedback",
        headers=auth_header(token),
        json={"rating": "down"},
    )
    assert resp.status_code == 404  # feedback is assistant-messages only (no oracle)


async def test_messages_paginate_by_seq(chat_harness: ChatHarness) -> None:
    token = await register_and_login(chat_harness.client, "acme")
    session_id = await create_session(chat_harness.client, token)
    await _send_grounded_turn(chat_harness, token, session_id)  # seq 0 (user) + 1 (assistant)

    first = (
        await chat_harness.client.get(
            f"/api/v1/chat/sessions/{session_id}/messages?limit=1", headers=auth_header(token)
        )
    ).json()
    assert len(first["messages"]) == 1
    assert first["messages"][0]["seq"] == 0
    assert first["next_cursor"] == 0

    second = (
        await chat_harness.client.get(
            f"/api/v1/chat/sessions/{session_id}/messages?limit=1&after_seq=0",
            headers=auth_header(token),
        )
    ).json()
    assert second["messages"][0]["seq"] == 1
    assert second["next_cursor"] is None


async def test_feedback_on_unknown_message_is_404(chat_harness: ChatHarness) -> None:
    token = await register_and_login(chat_harness.client, "acme")
    resp = await chat_harness.client.post(
        f"/api/v1/chat/messages/{uuid4()}/feedback",
        headers=auth_header(token),
        json={"rating": "up"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "MESSAGE_NOT_FOUND"


async def test_send_to_archived_session_is_409(chat_harness: ChatHarness) -> None:
    token = await register_and_login(chat_harness.client, "acme")
    session_id = await create_session(chat_harness.client, token)
    await chat_harness.client.patch(
        f"/api/v1/chat/sessions/{session_id}",
        headers=auth_header(token),
        json={"is_archived": True},
    )
    chat_harness.retriever.set_sources("source one")
    chat_harness.gateway.queue_stream(["Answer ", "[1]."])

    resp = await chat_harness.client.post(
        f"/api/v1/chat/sessions/{session_id}/messages",
        headers=auth_header(token),
        json={"content": "hello"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "SESSION_ARCHIVED"


async def test_per_user_rate_limit_blocks_after_cap(chat_harness: ChatHarness) -> None:
    token = await register_and_login(chat_harness.client, "acme")
    session_id = await create_session(chat_harness.client, token)

    # 30/min cap (CHAT_RATE_LIMIT). Each call scripts one source + one streamed answer.
    last_status = 200
    for _ in range(31):
        chat_harness.retriever.set_sources("source one")
        chat_harness.gateway.queue_stream(["Answer ", "[1]."])
        resp = await chat_harness.client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            headers=auth_header(token),
            json={"content": "hello"},
        )
        last_status = resp.status_code
    assert last_status == 429
