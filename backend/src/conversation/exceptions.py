"""Typed conversation-domain errors. Each carries a stable code + HTTP status; the
global handler renders them in the standard envelope (CLAUDE.md §5). A session a user
does not own is a 404 (NotFound), not a 403 — no existence oracle (consistent with the
identity/knowledge isolation posture)."""

from src.shared.errors import AppError


class SessionNotFoundError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__("SESSION_NOT_FOUND", 404, "Chat session not found", detail)


class SessionArchivedError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__("SESSION_ARCHIVED", 409, "Chat session is archived", detail)


class MessageTooLongError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__("MESSAGE_TOO_LONG", 422, "Message exceeds the length limit", detail)


class EmptyMessageError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__("MESSAGE_EMPTY", 422, "Message must not be empty", detail)


class SessionLimitExceededError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__("SESSION_LIMIT_EXCEEDED", 409, "Chat session limit reached", detail)


class InvalidCursorError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__("INVALID_CURSOR", 422, "Pagination cursor is malformed", detail)


class MessageNotFoundError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__("MESSAGE_NOT_FOUND", 404, "Chat message not found", detail)
