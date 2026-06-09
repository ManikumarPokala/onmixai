"""Pure identity business rules (no I/O) — see patterns.md §4.

Each rule takes data in and returns or raises; the service gathers the data.
Branch-complete unit tests live in tests/identity/test_rules.py.
"""

from datetime import datetime

from src.identity.models import RefreshToken, Role
from src.shared.errors import AuthorizationError, ValidationFailedError

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


def ensure_role_change_allowed(
    *, actor_role: Role, target_role: Role, new_role: Role, active_owner_count: int
) -> None:
    """Role-change policy (raises FORBIDDEN). Only an owner may grant the owner role or change an
    owner's role; the org must always keep at least one active owner. Time/Space: O(1)."""
    if new_role == Role.OWNER and actor_role != Role.OWNER:
        raise AuthorizationError("FORBIDDEN", message="Only an owner can grant the owner role")
    if target_role == Role.OWNER and actor_role != Role.OWNER:
        raise AuthorizationError("FORBIDDEN", message="Only an owner can change an owner's role")
    if target_role == Role.OWNER and new_role != Role.OWNER and active_owner_count <= 1:
        raise AuthorizationError(
            "FORBIDDEN", message="The organization must keep at least one owner"
        )


def ensure_deactivation_allowed(
    *, actor_role: Role, target_role: Role, active_owner_count: int
) -> None:
    """Deactivation policy (raises FORBIDDEN). Only an owner may deactivate an owner, and the last
    active owner can never be deactivated. Time/Space: O(1)."""
    if target_role == Role.OWNER and actor_role != Role.OWNER:
        raise AuthorizationError("FORBIDDEN", message="Only an owner can deactivate an owner")
    if target_role == Role.OWNER and active_owner_count <= 1:
        raise AuthorizationError("FORBIDDEN", message="Cannot deactivate the last owner")
