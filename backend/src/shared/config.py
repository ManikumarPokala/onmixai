"""Typed, fail-fast application configuration.

A single ``Settings`` class is the only place environment configuration enters the
codebase (CLAUDE.md §3.8 — no ``os.getenv`` anywhere else). Invalid or missing
configuration raises at construction time, so the application can never boot
half-configured. Access goes through ``get_settings()`` (cached) and is injected
via FastAPI dependencies, never imported as a module-level singleton in business
code.
"""

from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Known-insecure secrets that must never reach production. The first entry is the
# documented docker-compose dev default (infra/docker-compose.yml); booting prod
# with any of these is a fail-fast error, not a silent insecure start.
DENYLISTED_SECRETS: frozenset[str] = frozenset(
    {
        "dev-only-insecure-secret-change-me-32+chars",
    }
)

MIN_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """Application settings loaded from the environment / ``.env``.

    Field names map to upper-cased environment variables (e.g. ``jwt_secret`` ←
    ``JWT_SECRET``). Every variable is documented in ``.env.example``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env: Literal["dev", "test", "prod"]
    database_url: PostgresDsn

    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 1_209_600

    log_level: str = "INFO"

    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_pool_timeout_seconds: int = 30

    # Object storage (S3-compatible) and ingestion queue.
    storage_endpoint: str
    storage_access_key: SecretStr
    storage_secret_key: SecretStr
    storage_bucket: str
    redis_url: str

    # Ingestion limits and tuning.
    max_upload_bytes: int = 52_428_800
    max_document_pages: int = 2000
    embedding_dimension: int
    embedding_batch_size: int = 100
    # Chunking targets, in whitespace tokens (the token model the chunkers use).
    chunk_token_target: int = 512
    chunk_token_overlap: int = 64
    # Table-aware chunking: data rows per chunk (the header row is repeated in each).
    chunk_table_rows: int = 50
    ingest_max_attempts: int = 3
    ingest_stuck_after_seconds: int = 1800
    # Fault-injection knob for failure drills only (default 0 = off in prod):
    # an artificial pause inside ingestion so a kill/sweep drill can land mid-task.
    ingest_chaos_delay_seconds: float = 0.0

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_long_enough(cls, value: SecretStr) -> SecretStr:
        """Reject short signing secrets regardless of environment.

        Time: O(1). Space: O(1).
        """
        if len(value.get_secret_value()) < MIN_JWT_SECRET_LENGTH:
            raise ValueError(f"JWT_SECRET must be at least {MIN_JWT_SECRET_LENGTH} characters")
        return value

    @model_validator(mode="after")
    def _reject_denylisted_prod_secret(self) -> "Settings":
        """Forbid known/dev secrets when running in production.

        A secret present in DENYLISTED_SECRETS is acceptable in dev/test but a
        startup failure in prod — defense against shipping the committed dev
        default. Time: O(1) frozenset membership. Space: O(1).
        """
        if self.env == "prod" and self.jwt_secret.get_secret_value() in DENYLISTED_SECRETS:
            raise ValueError(
                "JWT_SECRET is a known/dev secret and must not be used when ENV=prod; "
                "set a unique production secret"
            )
        return self

    @model_validator(mode="after")
    def _reject_chaos_in_prod(self) -> "Settings":
        """Fault injection must be structurally impossible in production.

        A nonzero ingest_chaos_delay_seconds with ENV=prod is a startup failure
        (same posture as the dev-secret denylist). Time: O(1). Space: O(1).
        """
        if self.env == "prod" and self.ingest_chaos_delay_seconds != 0:
            raise ValueError(
                "INGEST_CHAOS_DELAY_SECONDS must be 0 when ENV=prod (fault injection "
                "is not allowed in production)"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton (constructed once, then cached).

    Construction validates all configuration and fails fast on the first bad or
    missing variable. Injected via FastAPI dependencies, never imported directly
    into business logic.
    """
    return Settings()


class _DimensionSettings(BaseSettings):
    """Reads only EMBEDDING_DIMENSION (the single source of truth for the vector
    column width) so ORM models and migrations can size the column without
    requiring the full Settings (storage/JWT/etc.) to be present."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    embedding_dimension: int


@lru_cache
def get_embedding_dimension() -> int:
    """Configured embedding/vector dimension; used by models and migration 0002."""
    return _DimensionSettings().embedding_dimension
