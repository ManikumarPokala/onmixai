"""LiteLLM adapter unit tests with an injected provider call (no network): model
resolution, retry/reject classification, and structured-output validation + re-ask.
The real-network resilience drills live in test_resilience.py."""

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import litellm
import pytest
from pydantic import BaseModel

from src.ai.adapters.circuit_breaker import CircuitBreaker
from src.ai.adapters.litellm_gateway import LiteLLMGateway
from src.ai.gateway import (
    ChatMessage,
    GatewayContext,
    ModelRef,
    RenderedPrompt,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from src.ai.models import ModelConfig, UsageFeature
from tests.ai.conftest import NoModelConfig, llm_settings


def _prompt() -> RenderedPrompt:
    return RenderedPrompt(
        template_name="t",
        template_version="1.0.0",
        messages=(ChatMessage("user", "hello"),),
        variables_hash="h",
    )


def _ctx() -> GatewayContext:
    return GatewayContext(
        org_id=uuid4(), user_id=uuid4(), feature=UsageFeature.CHAT, request_id="r"
    )


def _resp(content: str = "ok", *, pt: int = 3, ct: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=pt, completion_tokens=ct),
        model="echoed",
    )


class _FakeAcompletion:
    """Scriptable provider call: pops queued responses/exceptions, recording kwargs."""

    def __init__(self, outcomes: list[Any] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0) if self.outcomes else _resp()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


async def _noop_sleep(_seconds: float) -> None:
    return None


def _gateway(settings: Any, acompletion: Any, *, configs: Any = None) -> LiteLLMGateway:
    return LiteLLMGateway(
        settings=settings,
        configs=configs or NoModelConfig(),
        breaker=CircuitBreaker(failure_threshold=99, reset_seconds=60),
        acompletion=acompletion,
        sleep=_noop_sleep,
    )


# --- model resolution ---


async def test_explicit_model_overrides_config_and_settings() -> None:
    fake = _FakeAcompletion()
    gw = _gateway(llm_settings("http://x", default_model="settings/default"), fake)
    await gw.complete(prompt=_prompt(), ctx=_ctx(), model=ModelRef("explicit/model"))
    assert fake.calls[0]["model"] == "explicit/model"


async def test_org_config_overrides_settings_default() -> None:
    fake = _FakeAcompletion()
    config = ModelConfig(
        org_id=uuid4(), default_model="org/default", fallback_chain=[], temperature_default=0.2
    )

    class _Reader:
        async def get(self, org_id: Any) -> ModelConfig:
            return config

    gw = _gateway(
        llm_settings("http://x", default_model="settings/default"), fake, configs=_Reader()
    )
    await gw.complete(prompt=_prompt(), ctx=_ctx())
    assert fake.calls[0]["model"] == "org/default"
    assert fake.calls[0]["temperature"] == 0.2  # org temperature flows through


async def test_settings_default_and_chain_used_without_config() -> None:
    fake = _FakeAcompletion([litellm.ServiceUnavailableError("down", "openai", "m"), _resp("ok2")])
    settings = llm_settings(
        "http://x", default_model="settings/primary", fallback_chain=["settings/backup"], retries=0
    )
    gw = _gateway(settings, fake)
    await gw.complete(prompt=_prompt(), ctx=_ctx())
    assert [c["model"] for c in fake.calls] == ["settings/primary", "settings/backup"]


# --- retry / reject classification ---


async def test_retryable_error_retried_then_unavailable() -> None:
    err = litellm.RateLimitError("429", "openai", "m")
    fake = _FakeAcompletion([err, err, err])  # retries=2 → 3 attempts, all fail
    gw = _gateway(llm_settings("http://x", retries=2), fake)
    with pytest.raises(UpstreamUnavailableError):
        await gw.complete(prompt=_prompt(), ctx=_ctx())
    assert len(fake.calls) == 3  # one initial + two retries


async def test_provider_rejection_not_retried_and_no_fallback() -> None:
    fake = _FakeAcompletion([litellm.BadRequestError("bad", "openai", "m")])
    settings = llm_settings(
        "http://x", default_model="p", fallback_chain=["backup"], retries=2, threshold=99
    )
    gw = _gateway(settings, fake)
    with pytest.raises(UpstreamRejectedError):
        await gw.complete(prompt=_prompt(), ctx=_ctx())
    assert len(fake.calls) == 1  # rejected on the first attempt; no retry, no fallback


async def test_http_408_request_timeout_is_retryable() -> None:
    # 408 is a 4xx but semantically a timeout — retried like 429/5xx, not rejected.
    timeout_408 = litellm.APIError(408, "Request Timeout", llm_provider="openai", model="m")
    fake = _FakeAcompletion([timeout_408, _resp("recovered")])
    gw = _gateway(llm_settings("http://x", retries=1), fake)
    completion = await gw.complete(prompt=_prompt(), ctx=_ctx())
    assert completion.text == "recovered"
    assert len(fake.calls) == 2  # retried after the 408, not rejected on attempt 1


def test_worst_case_bound_includes_attempts_and_backoff() -> None:
    # The "never hangs" ceiling counts attempt timeouts AND inter-retry backoff.
    settings = llm_settings("http://x", retries=2, timeout=5)  # base 0.01, max 0.05
    gw = _gateway(settings, _FakeAcompletion())
    backoff = min(0.05, 0.01 * 1) + min(0.05, 0.01 * 2)  # two backoffs: 0.01 + 0.02
    expected = 2 * ((2 + 1) * 5 + backoff)  # chain × (attempts_ceiling + backoff) = 30.06
    assert gw.worst_case_wall_clock_seconds(2) == pytest.approx(expected)
    assert gw.worst_case_wall_clock_seconds(2) > 2 * (2 + 1) * 5  # strictly > attempts-only


# --- structured output ---


class _Answer(BaseModel):
    answer: str


async def test_structured_output_revalidates_after_one_reask() -> None:
    fake = _FakeAcompletion([_resp("not json"), _resp('{"answer": "ok"}')])
    gw = _gateway(llm_settings("http://x"), fake)
    completion = await gw.complete(prompt=_prompt(), ctx=_ctx(), response_schema=_Answer)
    assert _Answer.model_validate_json(completion.text).answer == "ok"
    assert len(fake.calls) == 2  # invalid → exactly one re-ask
    assert fake.calls[0]["response_format"] == {"type": "json_object"}


async def test_structured_output_rejects_after_failed_reask() -> None:
    fake = _FakeAcompletion([_resp("nope"), _resp("still not json")])
    gw = _gateway(llm_settings("http://x"), fake)
    with pytest.raises(UpstreamRejectedError) as exc_info:
        await gw.complete(prompt=_prompt(), ctx=_ctx(), response_schema=_Answer)
    assert exc_info.value.code == "SCHEMA_VALIDATION_FAILED"
    assert len(fake.calls) == 2  # one re-ask only, then a typed rejection
