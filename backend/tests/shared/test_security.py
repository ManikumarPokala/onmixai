"""JWT access-token signing/verification, including the dual-secret rotation grace window
(Phase 7 / Task 2): during a rotation, tokens signed by either the current or the previous secret
verify, so live sessions survive; once the previous secret is cleared, old tokens are rejected.
Pure — no DB."""

from uuid import uuid4

import jwt
import pytest
from pydantic import SecretStr

from src.shared.config import Settings
from src.shared.security import create_access_token, decode_access_token


def _settings(secret: str, *, previous: str | None = None) -> Settings:
    return Settings(
        _env_file=None,
        env="test",
        database_url="postgresql+asyncpg://u:p@localhost:5432/db",
        jwt_secret=SecretStr(secret),
        jwt_secret_previous=SecretStr(previous) if previous else None,
        storage_endpoint="http://localhost:9000",
        storage_access_key="a",
        storage_secret_key="s",
        storage_bucket="b",
        redis_url="redis://localhost:6379/0",
        embedding_dimension=8,
    )


_OLD = "old-secret-key-at-least-32-characters-long"
_NEW = "new-secret-key-at-least-32-characters-long"


def _token(secret: str) -> str:
    return create_access_token(
        settings=_settings(secret), user_id=uuid4(), org_id=uuid4(), role="member"
    )


def test_token_verifies_against_the_current_secret() -> None:
    claims = decode_access_token(_token(_NEW), settings=_settings(_NEW))
    assert claims["role"] == "member"


def test_old_token_survives_rotation_during_the_grace_window() -> None:
    # Rotation: previous = old, current = new. A token signed by the OLD secret still verifies.
    token = _token(_OLD)
    claims = decode_access_token(token, settings=_settings(_NEW, previous=_OLD))
    assert claims["role"] == "member"  # live session survives the rotation


def test_old_token_rejected_once_the_grace_window_closes() -> None:
    # Window closed: previous cleared. The old-secret token is no longer trusted.
    token = _token(_OLD)
    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(token, settings=_settings(_NEW, previous=None))


def test_expiry_failure_does_not_fall_through_to_the_previous_secret() -> None:
    # A current-secret token that is merely EXPIRED must raise ExpiredSignature, never get a second
    # chance against the previous secret (only signature failures use the grace path).
    import time

    s = _settings(_NEW, previous=_OLD).model_copy(update={"access_token_ttl_seconds": -1})
    token = create_access_token(settings=s, user_id=uuid4(), org_id=uuid4(), role="member")
    time.sleep(0.01)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token, settings=s)
