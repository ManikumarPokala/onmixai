"""Branch-complete tests for the pure conversation rules — including the ownership
NotFound (no-oracle) semantics."""

from uuid import uuid4

import pytest

from src.conversation.exceptions import (
    EmptyMessageError,
    MessageTooLongError,
    SessionArchivedError,
    SessionLimitExceededError,
    SessionNotFoundError,
)
from src.conversation.models import ChatSession
from src.conversation.rules import (
    derive_title,
    ensure_session_active,
    ensure_session_limit,
    ensure_session_owner,
    next_seq,
    validate_message_content,
)


def _session(owner: object, archived: bool = False) -> ChatSession:
    return ChatSession(owner_user_id=owner, is_archived=archived)


def test_ensure_session_owner_missing_or_other_user_is_not_found() -> None:
    user = uuid4()
    with pytest.raises(SessionNotFoundError):
        ensure_session_owner(None, user)  # missing
    with pytest.raises(SessionNotFoundError):
        ensure_session_owner(_session(uuid4()), user)  # another user's session → 404, no oracle


def test_ensure_session_owner_returns_owned_session() -> None:
    user = uuid4()
    session = _session(user)
    assert ensure_session_owner(session, user) is session


def test_ensure_session_active() -> None:
    ensure_session_active(_session(uuid4(), archived=False))  # no raise
    with pytest.raises(SessionArchivedError):
        ensure_session_active(_session(uuid4(), archived=True))


def test_ensure_session_limit() -> None:
    ensure_session_limit(199, 200)  # under cap → no raise
    with pytest.raises(SessionLimitExceededError):
        ensure_session_limit(200, 200)


def test_validate_message_content() -> None:
    assert validate_message_content("  hello   world  ", max_chars=100) == "hello world"
    with pytest.raises(EmptyMessageError):
        validate_message_content("   \n\t ", max_chars=100)
    with pytest.raises(MessageTooLongError):
        validate_message_content("x" * 101, max_chars=100)


def test_derive_title() -> None:
    assert derive_title("  Quarterly   revenue  review ") == "Quarterly revenue review"
    long = "word " * 40
    title = derive_title(long)
    assert len(title) <= 80 and title.endswith("…")


def test_next_seq() -> None:
    assert next_seq(None) == 0
    assert next_seq(0) == 1
    assert next_seq(7) == 8
