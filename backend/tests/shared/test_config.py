"""Tests for typed, fail-fast configuration (src/shared/config.py)."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from src.shared.config import DENYLISTED_SECRETS, Settings, get_settings

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
VALID_DSN = "postgresql+asyncpg://onmixai:onmixai@localhost:5432/onmixai"
DENYLISTED = next(iter(DENYLISTED_SECRETS))
STRONG_SECRET = "x" * 40


def _build(**overrides: Any) -> Settings:
    """Construct Settings from explicit values, ignoring any ambient .env."""
    return Settings(_env_file=None, **overrides)


def test_loads_from_env_example() -> None:
    settings = Settings(_env_file=str(ENV_EXAMPLE))
    assert settings.env == "dev"
    assert settings.jwt_algorithm == "HS256"
    assert len(settings.jwt_secret.get_secret_value()) >= 32
    assert settings.database_url.scheme == "postgresql+asyncpg"


def test_short_jwt_secret_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        _build(env="dev", database_url=VALID_DSN, jwt_secret="short")
    assert "JWT_SECRET" in str(exc.value)


def test_missing_required_fields_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("ENV", "DATABASE_URL", "JWT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValidationError) as exc:
        _build()
    message = str(exc.value)
    assert "env" in message and "database_url" in message and "jwt_secret" in message


def test_prod_rejects_denylisted_secret() -> None:
    with pytest.raises(ValidationError) as exc:
        _build(env="prod", database_url=VALID_DSN, jwt_secret=DENYLISTED)
    assert "ENV=prod" in str(exc.value)


def test_dev_allows_denylisted_secret() -> None:
    settings = _build(env="dev", database_url=VALID_DSN, jwt_secret=DENYLISTED)
    assert settings.jwt_secret.get_secret_value() == DENYLISTED


def test_prod_accepts_unique_secret() -> None:
    settings = _build(env="prod", database_url=VALID_DSN, jwt_secret=STRONG_SECRET)
    assert settings.env == "prod"


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("DATABASE_URL", VALID_DSN)
    monkeypatch.setenv("JWT_SECRET", STRONG_SECRET)
    get_settings.cache_clear()
    first = get_settings()
    second = get_settings()
    assert first is second
    get_settings.cache_clear()
