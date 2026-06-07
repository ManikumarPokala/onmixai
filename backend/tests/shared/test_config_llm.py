"""LLM gateway settings + the three Phase-3 prod guards (stub endpoint, empty
fallback chain, logging tracer opt-in). Each guard is tested in isolation by
satisfying the other two, so a failure pins the exact rule."""

import pytest
from pydantic import ValidationError

from tests.shared.test_config import STRONG_SECRET, VALID_DSN, _build

# A prod-valid baseline (real endpoint, non-empty chain, langfuse tracer): each
# guard test perturbs exactly one field away from this.
_PROD_OK = {
    "env": "prod",
    "jwt_secret": STRONG_SECRET,
    "database_url": VALID_DSN,
    "llm_base_url": "https://api.openai.com/v1",
    "llm_fallback_chain": ["openai/gpt-4o-mini"],
    "tracing_exporter": "langfuse",
}


def test_llm_defaults_present_and_typed() -> None:
    settings = _build(env="dev", database_url=VALID_DSN, jwt_secret=STRONG_SECRET)
    assert settings.llm_default_model == "openai/gpt-4o-mini"
    assert settings.llm_fallback_chain == []
    assert settings.llm_timeout_seconds == 30
    assert settings.llm_max_retries == 2
    assert settings.llm_circuit_failure_threshold == 5
    assert settings.llm_circuit_reset_seconds == 60
    assert settings.tracing_exporter == "logging"
    assert settings.tracing_logging_allowed_in_prod is False
    assert settings.llm_api_key is None  # absent provider keys default to None


def test_prod_baseline_constructs() -> None:
    settings = _build(**_PROD_OK)
    assert settings.env == "prod"


def test_dev_allows_stub_endpoint_and_empty_chain_and_logging() -> None:
    # The guards fire only in prod; dev boots against the stub with an empty chain.
    settings = _build(
        env="dev",
        database_url=VALID_DSN,
        jwt_secret=STRONG_SECRET,
        llm_base_url="http://llm-stub:8000/v1",
        llm_fallback_chain=[],
        tracing_exporter="logging",
    )
    assert settings.llm_base_url == "http://llm-stub:8000/v1"


@pytest.mark.parametrize(
    "stub_url",
    ["http://llm-stub:8000/v1", "http://localhost:9130/v1", "http://127.0.0.1:8000/v1"],
)
def test_prod_rejects_stub_or_local_llm_endpoint(stub_url: str) -> None:
    with pytest.raises(ValidationError, match="LLM_BASE_URL"):
        _build(**{**_PROD_OK, "llm_base_url": stub_url})


def test_prod_requires_non_empty_fallback_chain() -> None:
    with pytest.raises(ValidationError, match="LLM_FALLBACK_CHAIN"):
        _build(**{**_PROD_OK, "llm_fallback_chain": []})


def test_prod_logging_tracer_requires_explicit_optin() -> None:
    with pytest.raises(ValidationError, match="TRACING_LOGGING_ALLOWED_IN_PROD"):
        _build(**{**_PROD_OK, "tracing_exporter": "logging"})
    # The deliberate opt-in makes the logging tracer acceptable in prod.
    settings = _build(
        **{**_PROD_OK, "tracing_exporter": "logging", "tracing_logging_allowed_in_prod": True}
    )
    assert settings.tracing_exporter == "logging"
