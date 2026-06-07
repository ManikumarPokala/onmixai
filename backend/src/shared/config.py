"""Typed, fail-fast application configuration.

A single ``Settings`` class is the only place environment configuration enters the
codebase (CLAUDE.md §3.8 — no ``os.getenv`` anywhere else). Invalid or missing
configuration raises at construction time, so the application can never boot
half-configured. Access goes through ``get_settings()`` (cached) and is injected
via FastAPI dependencies, never imported as a module-level singleton in business
code.
"""

from dataclasses import dataclass
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

# Substrings that mark a URL as a local/dev/stub endpoint — forbidden for LLM
# providers in production (a stub or localhost endpoint in prod is a misconfiguration,
# not a fallback). Time: O(markers) membership over a lowercased URL.
_LOCAL_URL_MARKERS: tuple[str, ...] = (
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "-stub",
    "host.docker.internal",
)


def _is_local_or_stub_url(url: str) -> bool:
    lowered = url.lower()
    return any(marker in lowered for marker in _LOCAL_URL_MARKERS)


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
    # Embedding provider (OpenAI-compatible). The API key is optional so non-worker
    # processes and tests (which use the fake) construct Settings without it; the
    # real adapter validates its presence when built.
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: SecretStr | None = None
    embedding_base_url: str | None = None
    embedding_timeout_seconds: float = 30.0
    embedding_max_attempts: int = 3
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

    # Search / retrieval (Phase 2). The single source of truth for HNSW + FTS index
    # build params (read by migration 0004 via get_index_params) and the runtime
    # retrieval knobs. No magic numbers elsewhere (CLAUDE.md §3.8, §7).
    search_hnsw_m: int = 16
    search_hnsw_ef_construction: int = 64
    # Runtime HNSW probe breadth. The Task-7 sweep showed latency is a non-constraint
    # (ef=200 → ~11 ms p95 @ 100k/1536, ~270x under budget) and recall rises with ef;
    # synthetic uniform vectors can't pin real recall, so we pick the recall-safest end
    # of the grid. Real-embedding tuning on a labeled set is a revisit trigger (ADR 0009).
    search_ef_search: int = 200
    # Filtered-ANN scan mode (pgvector >= 0.8). The vector arm applies the org+ACL
    # predicate before ranking, so the HNSW scan must keep fetching ordered
    # candidates until top_k survive the filter — otherwise a partial-access user
    # silently gets fewer than top_k. "strict_order" preserves exact distance order
    # (RRF ranks depend on it); see ADR 0009.
    search_hnsw_iterative_scan: str = "strict_order"
    search_top_k: int = 60  # candidates fetched per arm before fusion
    search_rrf_k: int = 60  # reciprocal-rank-fusion constant
    search_fts_language: str = "english"  # Postgres text-search config
    search_max_results: int = 50  # hard server-side page cap

    # AI / LLM gateway (Phase 3). The single source for provider routing and
    # resilience; the adapter is the only consumer (CLAUDE.md §3.6, §3.8).
    llm_default_model: str = "openai/gpt-4o-mini"
    # Ordered model refs tried after the default fails. Empty is fine in dev/test;
    # a prod guard requires it non-empty (no single point of failure in prod).
    llm_fallback_chain: list[str] = []
    llm_timeout_seconds: int = 30
    llm_max_retries: int = 2
    # Exponential backoff with full jitter between retries of one model. The worst-case
    # wall clock is bounded (chain × (retries+1) × timeout) so the gateway never hangs.
    llm_backoff_base_seconds: float = 0.5
    llm_backoff_max_seconds: float = 8.0
    llm_circuit_failure_threshold: int = 5
    llm_circuit_reset_seconds: int = 60
    llm_temperature_default: float = 0.7
    # OpenAI-compatible endpoint override (dev → llm-stub). Per-provider keys are
    # optional: an absent provider key means that provider is unavailable for any
    # chain (enforced in the adapter, Task 4) — never a silent unauthenticated call.
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    azure_api_key: SecretStr | None = None

    # Tracing (Phase 3). `logging` is dev-complete; `langfuse` for prod. Using the
    # logging exporter in prod is a deliberate opt-in (a prod guard requires it).
    tracing_exporter: Literal["logging", "langfuse"] = "logging"
    tracing_logging_allowed_in_prod: bool = False
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str | None = None

    # Conversation / chat (Phase 4). Limits the pipeline + assembly read; no magic
    # numbers elsewhere (CLAUDE.md §3.8).
    chat_max_sessions_per_user: int = 200
    chat_message_max_chars: int = 8000
    chat_history_turns: int = 10  # last-N turns kept in the assembled context
    chat_summary_threshold_turns: int = 16  # session length that triggers a rolling summary
    chat_context_token_budget: int = 6000  # max assembled-context tokens
    chat_confidence_min_score: float = 0.0  # below → refuse before generating (Task 5)
    chat_confidence_min_results: int = 1  # fewer retrieved → refuse before generating

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

    @model_validator(mode="after")
    def _reject_stub_llm_endpoint_in_prod(self) -> "Settings":
        """A stub/localhost LLM endpoint must be structurally impossible in prod.

        Time: O(1). Space: O(1).
        """
        if self.env == "prod" and self.llm_base_url and _is_local_or_stub_url(self.llm_base_url):
            raise ValueError(
                "LLM_BASE_URL must not point at a stub/localhost endpoint when ENV=prod"
            )
        return self

    @model_validator(mode="after")
    def _require_llm_fallback_chain_in_prod(self) -> "Settings":
        """Production must have a fallback chain (no single point of failure).

        Time: O(1). Space: O(1).
        """
        if self.env == "prod" and not self.llm_fallback_chain:
            raise ValueError("LLM_FALLBACK_CHAIN must be non-empty when ENV=prod")
        return self

    @model_validator(mode="after")
    def _require_explicit_logging_tracer_in_prod(self) -> "Settings":
        """The logging tracer in prod must be a deliberate opt-in, not the default.

        Time: O(1). Space: O(1).
        """
        if (
            self.env == "prod"
            and self.tracing_exporter == "logging"
            and not self.tracing_logging_allowed_in_prod
        ):
            raise ValueError(
                "tracing_exporter='logging' in prod requires "
                "TRACING_LOGGING_ALLOWED_IN_PROD=true (deliberate opt-in)"
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


@dataclass(frozen=True, slots=True)
class IndexParams:
    """Build-time index parameters shared by the ORM model and migration 0004."""

    hnsw_m: int
    hnsw_ef_construction: int
    fts_language: str


class _IndexParamSettings(BaseSettings):
    """Reads only the index build params so migration 0004 and the chunk model can
    agree on HNSW/FTS configuration without constructing the full Settings."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    search_hnsw_m: int = 16
    search_hnsw_ef_construction: int = 64
    search_fts_language: str = "english"


@lru_cache
def get_index_params() -> IndexParams:
    """HNSW/FTS build params — the single source of truth for migration 0004 and the
    chunks tsvector column (mirrors get_embedding_dimension)."""
    settings = _IndexParamSettings()
    return IndexParams(
        hnsw_m=settings.search_hnsw_m,
        hnsw_ef_construction=settings.search_hnsw_ef_construction,
        fts_language=settings.search_fts_language,
    )
