"""Resilience drills — the real litellm adapter against the in-process stub's fault
injection. Proves the four behaviours the Phase-3 exit criteria require: fallback
order, all-down → typed 503 within the computed wall-clock bound, circuit open/skip
(call-count proof), and half-open recovery/re-open. ``REQUEST_LOG`` is the provider
call log; the breaker uses an injected clock for deterministic time control."""

import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.ai.adapters.circuit_breaker import CircuitBreaker, CircuitState
from src.ai.adapters.litellm_gateway import LiteLLMGateway
from src.ai.gateway import (
    ChatMessage,
    GatewayContext,
    RenderedPrompt,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from src.ai.models import UsageFeature
from src.shared.config import Settings
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


def _gw(settings: Settings, breaker: CircuitBreaker) -> LiteLLMGateway:
    return LiteLLMGateway(settings=settings, configs=NoModelConfig(), breaker=breaker)


async def test_drill_primary_fails_then_fallback_succeeds(llm_stub: SimpleNamespace) -> None:
    settings = llm_settings(
        llm_stub.base_url,
        default_model="openai/srvfail",
        fallback_chain=["openai/okmodel"],
        retries=1,
        threshold=99,
    )
    completion = await _gw(settings, CircuitBreaker(99, 60)).complete(prompt=_prompt(), ctx=_ctx())
    assert completion.model_used == "openai/okmodel"  # succeeded via the fallback
    models = [r["model"] for r in llm_stub.module.REQUEST_LOG]
    assert len(models) == 3  # primary × (retries+1=2), then fallback × 1
    assert all("fail" in m for m in models[:2]) and "fail" not in models[2]  # order proven


async def test_drill_all_down_typed_503_within_bound(llm_stub: SimpleNamespace) -> None:
    settings = llm_settings(
        llm_stub.base_url,
        default_model="openai/afail",
        fallback_chain=["openai/bfail"],
        retries=1,
        timeout=5,
        threshold=99,
    )
    gateway = _gw(settings, CircuitBreaker(99, 60))
    bound = gateway.worst_case_wall_clock_seconds(chain_length=2)  # 2 × (1+1) × 5 = 20.0s
    started = time.monotonic()
    with pytest.raises(UpstreamUnavailableError):
        await gateway.complete(prompt=_prompt(), ctx=_ctx())
    elapsed = time.monotonic() - started
    assert elapsed < bound  # never hangs — terminates well under the computed bound
    assert len(llm_stub.module.REQUEST_LOG) == 4  # 2 models × (retries+1)


async def test_drill_circuit_opens_after_threshold_and_skips(llm_stub: SimpleNamespace) -> None:
    settings = llm_settings(llm_stub.base_url, default_model="openai/cfail", retries=0, threshold=2)
    breaker = CircuitBreaker(failure_threshold=2, reset_seconds=60)
    gateway = _gw(settings, breaker)
    for _ in range(2):  # two failed completions → two recorded failures → OPEN
        with pytest.raises(UpstreamUnavailableError):
            await gateway.complete(prompt=_prompt(), ctx=_ctx())
    requests_before = len(llm_stub.module.REQUEST_LOG)
    assert requests_before == 2  # retries=0 → exactly one provider call per completion
    assert breaker.state("openai/cfail") == CircuitState.OPEN

    with pytest.raises(UpstreamUnavailableError):
        await gateway.complete(prompt=_prompt(), ctx=_ctx())  # circuit OPEN → skipped
    assert len(llm_stub.module.REQUEST_LOG) == requests_before  # no new provider call


def test_drill_circuit_half_open_recovers_then_can_reopen() -> None:
    clock = SimpleNamespace(t=0.0)
    breaker = CircuitBreaker(failure_threshold=2, reset_seconds=10, clock=lambda: clock.t)
    breaker.record_failure("m")
    breaker.record_failure("m")
    assert breaker.state("m") == CircuitState.OPEN
    assert breaker.allow("m") is False  # skipped while open
    clock.t = 11.0  # past the reset window
    assert breaker.allow("m") is True  # a single half-open probe is released
    assert breaker.state("m") == CircuitState.HALF_OPEN
    breaker.record_success("m")  # probe succeeds → closed
    assert breaker.state("m") == CircuitState.CLOSED

    reopen = CircuitBreaker(failure_threshold=1, reset_seconds=10, clock=lambda: clock.t)
    clock.t = 0.0
    reopen.record_failure("m")  # threshold 1 → OPEN
    clock.t = 11.0
    assert reopen.allow("m") is True  # half-open probe
    reopen.record_failure("m")  # probe fails → re-OPEN immediately
    assert reopen.state("m") == CircuitState.OPEN
    assert reopen.allow("m") is False


async def test_drill_no_retry_on_provider_rejection(llm_stub: SimpleNamespace) -> None:
    settings = llm_settings(
        llm_stub.base_url,
        default_model="openai/rejectme",
        fallback_chain=["openai/okmodel"],
        retries=2,
        threshold=99,
    )
    with pytest.raises(UpstreamRejectedError):
        await _gw(settings, CircuitBreaker(99, 60)).complete(prompt=_prompt(), ctx=_ctx())
    assert len(llm_stub.module.REQUEST_LOG) == 1  # rejected on attempt 1; no retry, no fallback
