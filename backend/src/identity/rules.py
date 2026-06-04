"""Pure identity business rules (no I/O) — see patterns.md §4.

Each rule takes data in and returns or raises; the service gathers the data.
Branch-complete unit tests live in tests/identity/test_rules.py.
"""

from datetime import datetime

from src.identity.models import RefreshToken
from src.shared.errors import ValidationFailedError

MIN_PASSWORD_LENGTH = 12


def ensure_password_strong(password: str) -> None:
    """Enforce the minimum password policy. Time/Space: O(1)."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationFailedError(
            "WEAK_PASSWORD",
            message=f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
        )


def is_refresh_token_reused(token: RefreshToken) -> bool:
    """True if a presented, unexpired token was already revoked (theft signal)."""
    return token.revoked_at is not None


def is_refresh_token_expired(token: RefreshToken, now: datetime) -> bool:
    """True if the token is past its expiry at ``now``. Time/Space: O(1)."""
    return token.expires_at <= now
