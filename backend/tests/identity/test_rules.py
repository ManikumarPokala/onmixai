"""Branch-complete unit tests for identity pure rules (no I/O)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.identity.models import RefreshToken
from src.identity.rules import (
    MIN_PASSWORD_LENGTH,
    ensure_password_strong,
    is_refresh_token_expired,
    is_refresh_token_reused,
)
from src.shared.errors import ValidationFailedError


def _token(*, revoked: bool, expires_in: int) -> RefreshToken:
    now = datetime.now(UTC)
    return RefreshToken(
        org_id=uuid4(),
        user_id=uuid4(),
        token_hash="h",
        expires_at=now + timedelta(seconds=expires_in),
        revoked_at=now if revoked else None,
    )


def test_password_at_minimum_length_passes() -> None:
    ensure_password_strong("x" * MIN_PASSWORD_LENGTH)


def test_password_below_minimum_rejected() -> None:
    with pytest.raises(ValidationFailedError):
        ensure_password_strong("x" * (MIN_PASSWORD_LENGTH - 1))


def test_reuse_detected_only_when_revoked() -> None:
    assert is_refresh_token_reused(_token(revoked=True, expires_in=60)) is True
    assert is_refresh_token_reused(_token(revoked=False, expires_in=60)) is False


def test_expiry_boundary() -> None:
    now = datetime.now(UTC)
    assert is_refresh_token_expired(_token(revoked=False, expires_in=-1), now) is True
    assert is_refresh_token_expired(_token(revoked=False, expires_in=60), now) is False
