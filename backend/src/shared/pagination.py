"""Opaque keyset pagination cursor — a base64 (created_at, id) pair for list endpoints that
page newest-first over a (created_at, id) ordering. A malformed cursor is a typed 422, never a
500. Shared because several domains page this way."""

import base64
import binascii
from datetime import datetime
from uuid import UUID

from src.shared.errors import AppError


class InvalidCursorError(AppError):
    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__("INVALID_CURSOR", 422, "Pagination cursor is malformed", detail)


def encode_keyset_cursor(created_at: datetime, row_id: UUID) -> str:
    """Encode the (created_at, id) keyset position. Time/Space: O(1)."""
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{row_id}".encode()).decode()


def decode_keyset_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Inverse of :func:`encode_keyset_cursor`. Raises INVALID_CURSOR on any malformed input.
    Time/Space: O(1)."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts_str, sep, id_str = raw.partition("|")
        if not sep:
            raise ValueError("missing separator")
        return datetime.fromisoformat(ts_str), UUID(id_str)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise InvalidCursorError(detail="could not decode cursor") from exc
