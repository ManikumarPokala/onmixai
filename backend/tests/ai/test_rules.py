"""Branch-complete tests for ai.rules model-config validation (pure, no I/O — CLAUDE.md §9)."""

import pytest

from src.ai.rules import ensure_valid_model_config
from src.shared.errors import ValidationFailedError


def test_accepts_known_providers_with_a_fallback() -> None:
    ensure_valid_model_config(
        "openai/gpt-4o-mini", ["anthropic/claude-3-5-sonnet-latest", "azure/gpt-4o"]
    )  # no raise


@pytest.mark.parametrize(
    "default_model",
    ["bogus/model", "gpt-4o-mini", "openai/", "", "/gpt-4o-mini"],
)
def test_rejects_bad_default_model(default_model: str) -> None:
    with pytest.raises(ValidationFailedError) as exc:
        ensure_valid_model_config(default_model, ["openai/gpt-4o-mini"])
    assert exc.value.code == "INVALID_MODEL_CONFIG"


def test_rejects_empty_fallback_chain() -> None:
    with pytest.raises(ValidationFailedError) as exc:
        ensure_valid_model_config("openai/gpt-4o-mini", [])
    assert exc.value.code == "INVALID_MODEL_CONFIG"


@pytest.mark.parametrize("bad_entry", ["bogus/model", "openai/", "no-slash", ""])
def test_rejects_bad_fallback_entry(bad_entry: str) -> None:
    with pytest.raises(ValidationFailedError) as exc:
        ensure_valid_model_config("openai/gpt-4o-mini", ["anthropic/claude-3-5-sonnet", bad_entry])
    assert exc.value.code == "INVALID_MODEL_CONFIG"
