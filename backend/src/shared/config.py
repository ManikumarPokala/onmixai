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


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton (constructed once, then cached).

    Construction validates all configuration and fails fast on the first bad or
    missing variable. Injected via FastAPI dependencies, never imported directly
    into business logic.
    """
    return Settings()
