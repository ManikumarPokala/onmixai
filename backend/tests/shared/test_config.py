"""Tests for typed, fail-fast configuration (src/shared/config.py)."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from src.shared.config import (
    DENYLISTED_SECRETS,
    Settings,
    get_embedding_dimension,
    get_index_params,
    get_settings,
)

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
VALID_DSN = "postgresql+asyncpg://onmixai:onmixai@localhost:5432/onmixai"
DENYLISTED = next(iter(DENYLISTED_SECRETS))
STRONG_SECRET = "x" * 40

# Required infra settings, defaulted so each test only specifies what it exercises.
_INFRA_DEFAULTS: dict[str, Any] = {
    "storage_endpoint": "http://localhost:9000",
    "storage_access_key": "access",
    "storage_secret_key": "secret",
    "storage_bucket": "bucket",
    "redis_url": "redis://localhost:6379/0",
    "embedding_dimension": 8,
}


def _build(**overrides: Any) -> Settings:
    """Construct Settings from explicit values, ignoring any ambient .env."""
    return Settings(_env_file=None, **{**_INFRA_DEFAULTS, **overrides})


def test_loads_from_env_example(monkeypatch: pytest.MonkeyPatch) -> None:
    # Verify .env.example itself parses to the documented defaults. pydantic-settings reads the
    # OS environment OVER _env_file, so clear every var the file defines first — otherwise an
    # ambient/CI value (e.g. the test job's ENV=test) shadows the file and this asserts the
    # environment, not the example.
    for raw in ENV_EXAMPLE.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            monkeypatch.delenv(line.split("=", 1)[0].strip(), raising=False)
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
    # A prod-valid config also needs a fallback chain and a non-logging tracer
    # (Phase-3 prod guards); here we assert a unique secret is accepted.
    settings = _build(
        env="prod",
        database_url=VALID_DSN,
        jwt_secret=STRONG_SECRET,
        llm_fallback_chain=["openai/gpt-4o-mini"],
        tracing_exporter="langfuse",
    )
    assert settings.env == "prod"


def test_prod_rejects_nonzero_chaos_delay() -> None:
    with pytest.raises(ValidationError) as exc:
        _build(
            env="prod",
            database_url=VALID_DSN,
            jwt_secret=STRONG_SECRET,
            ingest_chaos_delay_seconds=2.0,
        )
    assert "INGEST_CHAOS_DELAY_SECONDS" in str(exc.value)


def test_dev_allows_chaos_delay() -> None:
    settings = _build(
        env="dev",
        database_url=VALID_DSN,
        jwt_secret=STRONG_SECRET,
        ingest_chaos_delay_seconds=5.0,
    )
    assert settings.ingest_chaos_delay_seconds == 5.0


def test_embedding_dimension_single_source(monkeypatch: pytest.MonkeyPatch) -> None:
    # get_embedding_dimension() and Settings.embedding_dimension read the same env
    # var and must never drift — the vector(N) column hangs off this one value.
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("DATABASE_URL", VALID_DSN)
    monkeypatch.setenv("JWT_SECRET", STRONG_SECRET)
    monkeypatch.setenv("STORAGE_ENDPOINT", "http://localhost:9000")
    monkeypatch.setenv("STORAGE_ACCESS_KEY", "access")
    monkeypatch.setenv("STORAGE_SECRET_KEY", "secret")
    monkeypatch.setenv("STORAGE_BUCKET", "bucket")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "321")
    get_settings.cache_clear()
    get_embedding_dimension.cache_clear()
    assert get_embedding_dimension() == get_settings().embedding_dimension == 321
    get_settings.cache_clear()
    get_embedding_dimension.cache_clear()


def test_search_tuning_knobs_present_and_typed() -> None:
    settings = _build(env="dev", database_url=VALID_DSN, jwt_secret=STRONG_SECRET)
    assert settings.search_hnsw_m == 16
    assert settings.search_hnsw_ef_construction == 64
    assert settings.search_ef_search == 200
    assert settings.search_hnsw_iterative_scan == "strict_order"
    assert settings.search_top_k == 60
    assert settings.search_rrf_k == 60
    assert settings.search_fts_language == "english"
    assert settings.search_max_results == 50


def test_index_params_single_source(monkeypatch: pytest.MonkeyPatch) -> None:
    # get_index_params() (read by migration 0004 + the chunks model) and Settings
    # must never drift — the HNSW/GIN index build params hang off these values.
    for key, value in {
        "ENV": "test",
        "DATABASE_URL": VALID_DSN,
        "JWT_SECRET": STRONG_SECRET,
        "STORAGE_ENDPOINT": "http://localhost:9000",
        "STORAGE_ACCESS_KEY": "access",
        "STORAGE_SECRET_KEY": "secret",
        "STORAGE_BUCKET": "bucket",
        "REDIS_URL": "redis://localhost:6379/0",
        "EMBEDDING_DIMENSION": "8",
        "SEARCH_HNSW_M": "24",
        "SEARCH_HNSW_EF_CONSTRUCTION": "100",
        "SEARCH_FTS_LANGUAGE": "simple",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    get_index_params.cache_clear()
    params = get_index_params()
    settings = get_settings()
    assert params.hnsw_m == settings.search_hnsw_m == 24
    assert params.hnsw_ef_construction == settings.search_hnsw_ef_construction == 100
    assert params.fts_language == settings.search_fts_language == "simple"
    get_settings.cache_clear()
    get_index_params.cache_clear()


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("DATABASE_URL", VALID_DSN)
    monkeypatch.setenv("JWT_SECRET", STRONG_SECRET)
    monkeypatch.setenv("STORAGE_ENDPOINT", "http://localhost:9000")
    monkeypatch.setenv("STORAGE_ACCESS_KEY", "access")
    monkeypatch.setenv("STORAGE_SECRET_KEY", "secret")
    monkeypatch.setenv("STORAGE_BUCKET", "bucket")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "8")
    get_settings.cache_clear()
    first = get_settings()
    second = get_settings()
    assert first is second
    get_settings.cache_clear()
