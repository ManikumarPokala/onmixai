"""Pure conversation rules (no I/O) — ownership, lifecycle, message validation, title
derivation, sequencing. Each is independently branch-testable (patterns.md §4). Limits
are passed in by the service from Settings (no magic numbers here)."""

from uuid import UUID

from src.conversation.exceptions import (
    EmptyMessageError,
    MessageTooLongError,
    SessionArchivedError,
    SessionLimitExceededError,
    SessionNotFoundError,
)
from src.conversation.models import ChatSession

_TITLE_MAX = 80


def ensure_session_owner(session: ChatSession | None, user_id: UUID) -> ChatSession:
    """Return ``session`` iff it exists and is owned by ``user_id``; otherwise raise
    SESSION_NOT_FOUND. A session owned by another user (even same org) is indistinguishable
    from a missing one — no existence oracle. Time/Space: O(1)."""
    if session is None or session.owner_user_id != user_id:
        raise SessionNotFoundError()
    return session


def ensure_session_active(session: ChatSession) -> None:
    """Reject sending to an archived session (409). Time/Space: O(1)."""
    if session.is_archived:
        raise SessionArchivedError()


def ensure_session_limit(current_count: int, limit: int) -> None:
    """Reject creating a session past the per-user cap (409). Time/Space: O(1)."""
    if current_count >= limit:
        raise SessionLimitExceededError(detail=f"limit {limit} reached")


def validate_message_content(content: str, *, max_chars: int) -> str:
    """Normalize (collapse whitespace) and bound a user message to 1..max_chars,
    returning the normalized text. Raises MESSAGE_EMPTY / MESSAGE_TOO_LONG.
    Time: O(n). Space: O(n)."""
    normalized = " ".join(content.split())
    if not normalized:
        raise EmptyMessageError()
    if len(normalized) > max_chars:
        raise MessageTooLongError(detail=f"{len(normalized)} > {max_chars}")
    return normalized


def derive_title(first_user_message: str) -> str:
    """A session title from the first user message: whitespace-collapsed, truncated.
    Time: O(n). Space: O(n)."""
    normalized = " ".join(first_user_message.split())
    if len(normalized) <= _TITLE_MAX:
        return normalized
    return normalized[: _TITLE_MAX - 1].rstrip() + "…"


def next_seq(current_max: int | None) -> int:
    """The next per-session message seq (0-based, monotonic). Time/Space: O(1)."""
    return 0 if current_max is None else current_max + 1
