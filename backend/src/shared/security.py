"""Cross-cutting security primitives: password hashing, JWTs, refresh tokens.

argon2id parameters are fixed security constants (not env config). JWT helpers
take the relevant ``Settings`` explicitly rather than reading a global. Decode
raises the underlying ``jwt`` errors; the identity layer maps them to
``AuthenticationError`` (so this module stays free of web concerns).
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from src.shared.config import Settings

# argon2id work factors (CLAUDE.md / Sprint 1 Task 6): time_cost=3,
# memory_cost=64 MiB (65536 KiB), parallelism=4.
_ARGON2_TIME_COST = 3
_ARGON2_MEMORY_COST_KIB = 65536
_ARGON2_PARALLELISM = 4
_REFRESH_TOKEN_BYTES = 32

_password_hasher = PasswordHasher(
    time_cost=_ARGON2_TIME_COST,
    memory_cost=_ARGON2_MEMORY_COST_KIB,
    parallelism=_ARGON2_PARALLELISM,
)


def hash_password(password: str) -> str:
    """Hash a plaintext password with argon2id. Time/Space: O(1) in input length."""
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Constant-time verify of a password against its argon2id hash."""
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """True if the stored hash was produced with weaker-than-current parameters."""
    return _password_hasher.check_needs_rehash(password_hash)


def create_access_token(*, settings: Settings, user_id: UUID, org_id: UUID, role: str) -> str:
    """Mint a signed access JWT with sub/org_id/role/iat/exp/jti claims.

    Time/Space: O(1).
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "org_id": str(org_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(seconds=settings.access_token_ttl_seconds),
        "jti": uuid4().hex,
    }
    return jwt.encode(
        payload, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )


def decode_access_token(token: str, *, settings: Settings) -> dict[str, Any]:
    """Verify signature + expiry (no leeway) and return the claims.

    Raises ``jwt.PyJWTError`` subclasses on any failure; callers map these to
    ``AuthenticationError``. Time/Space: O(1).
    """
    return jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
        leeway=0,
        options={"require": ["exp", "iat", "sub"]},
    )


def generate_refresh_token() -> str:
    """Return a fresh opaque refresh token (URL-safe, 32 bytes of entropy)."""
    return secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)


def hash_refresh_token(raw_token: str) -> str:
    """Return the SHA-256 hex digest stored in place of a raw refresh token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
