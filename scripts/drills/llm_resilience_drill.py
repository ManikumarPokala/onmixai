#!/usr/bin/env python
"""LLM gateway resilience drill — human-readable evidence for the Phase-3 Task-4 pause.

Runs the REAL litellm adapter against the in-process dev stub's fault injection and
prints the four drill outcomes plus the computed wall-clock bound. The same behaviours
are gated deterministically in tests/ai/test_resilience.py; this script is the
printable artifact (run: backend/.venv/bin/python scripts/drills/llm_resilience_drill.py).
"""

from __future__ import annotations

import asyncio
import importlib.util
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

import litellm

from src.ai.adapters.circuit_breaker import CircuitBreaker
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

litellm.disable_aiohttp_transport = True
_STUB = Path(__file__).resolve().parents[2] / "infra" / "dev" / "llm_stub.py"


def _load_stub() -> Any:
    spec = importlib.util.spec_from_file_location("llm_stub", _STUB)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _settings(base_url: str, *, default: str, fallback: list[str], retries: int, timeout: int,
              threshold: int) -> Settings:
    return Settings(
        _env_file=None, env="test",
        database_url="postgresql+asyncpg://onmixai:onmixai@localhost:5432/onmixai",
        jwt_secret="x" * 40, storage_endpoint="http://localhost:9000", storage_access_key="a",
        storage_secret_key="s", storage_bucket="b", redis_url="redis://localhost:6379/0",
        embedding_dimension=8, llm_base_url=base_url, llm_api_key="k",
        llm_default_model=default, llm_fallback_chain=fallback, llm_timeout_seconds=timeout,
        llm_max_retries=retries, llm_backoff_base_seconds=0.01, llm_backoff_max_seconds=0.05,
        llm_circuit_failure_threshold=threshold, llm_circuit_reset_seconds=60,
    )


class _NoConfig:
    async def get(self, org_id: Any) -> None:
        return None


def _prompt() -> RenderedPrompt:
    return RenderedPrompt("t", "1.0.0", (ChatMessage("user", "hello"),), "h")


def _ctx() -> GatewayContext:
    return GatewayContext(uuid4(), uuid4(), UsageFeature.CHAT, "r")


async def main() -> int:
    stub = _load_stub()
    server = ThreadingHTTPServer(("127.0.0.1", 0), stub._Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/v1"
    try:
        # Drill 1 — primary fails, fallback succeeds (order from the provider call log).
        stub.REQUEST_LOG.clear()
        s1 = _settings(base, default="openai/srvfail", fallback=["openai/okmodel"], retries=1,
                       timeout=5, threshold=99)
        gw1 = LiteLLMGateway(settings=s1, configs=_NoConfig(), breaker=CircuitBreaker(99, 60))
        completion = await gw1.complete(prompt=_prompt(), ctx=_ctx())
        order = [r["model"] for r in stub.REQUEST_LOG]
        print(f"[drill 1] fallback order: calls={order}  -> succeeded via {completion.model_used}")

        # Drill 2 — all providers down → typed 503 within the computed wall-clock bound.
        stub.REQUEST_LOG.clear()
        s2 = _settings(base, default="openai/afail", fallback=["openai/bfail"], retries=1,
                       timeout=5, threshold=99)
        gw2 = LiteLLMGateway(settings=s2, configs=_NoConfig(), breaker=CircuitBreaker(99, 60))
        bound = gw2.worst_case_wall_clock_seconds(2)
        started = time.monotonic()
        try:
            await gw2.complete(prompt=_prompt(), ctx=_ctx())
            outcome = "NO ERROR (unexpected)"
        except UpstreamUnavailableError:
            outcome = "UpstreamUnavailableError (503)"
        elapsed = time.monotonic() - started
        print(f"[drill 2] all down: bound={bound:.1f}s  raised {outcome} in {elapsed:.3f}s "
              f"(< bound={elapsed < bound})  provider_calls={len(stub.REQUEST_LOG)}")

        # Drill 3 — circuit opens after threshold, then SKIPS without a provider call.
        stub.REQUEST_LOG.clear()
        s3 = _settings(base, default="openai/cfail", fallback=[], retries=0, timeout=5, threshold=2)
        breaker = CircuitBreaker(failure_threshold=2, reset_seconds=60)
        gw3 = LiteLLMGateway(settings=s3, configs=_NoConfig(), breaker=breaker)
        for _ in range(2):
            try:
                await gw3.complete(prompt=_prompt(), ctx=_ctx())
            except UpstreamUnavailableError:
                pass
        before = len(stub.REQUEST_LOG)
        try:
            await gw3.complete(prompt=_prompt(), ctx=_ctx())
        except UpstreamUnavailableError:
            pass
        after = len(stub.REQUEST_LOG)
        print(f"[drill 3] circuit: provider_calls before open={before}, state={breaker.state('openai/cfail')}; "
              f"after open={after} (skipped, +{after - before})")

        # Drill 4 — half-open recovery and probe-failure re-open (deterministic clock).
        clock = {"t": 0.0}
        b4 = CircuitBreaker(failure_threshold=2, reset_seconds=10, clock=lambda: clock["t"])
        b4.record_failure("m")
        b4.record_failure("m")
        line = [f"open(allow={b4.allow('m')})"]
        clock["t"] = 11.0
        line.append(f"reset->allow={b4.allow('m')}/{b4.state('m')}")
        b4.record_success("m")
        line.append(f"probe-success->{b4.state('m')}")
        b5 = CircuitBreaker(failure_threshold=1, reset_seconds=10, clock=lambda: clock["t"])
        clock["t"] = 0.0
        b5.record_failure("m")
        clock["t"] = 11.0
        b5.allow("m")
        b5.record_failure("m")
        line.append(f"probe-fail->{b5.state('m')}")
        print(f"[drill 4] half-open: {' '.join(line)}")

        # No-retry-on-rejection (a provider 4xx is surfaced immediately).
        stub.REQUEST_LOG.clear()
        s5 = _settings(base, default="openai/rejectme", fallback=["openai/okmodel"], retries=2,
                       timeout=5, threshold=99)
        gw5 = LiteLLMGateway(settings=s5, configs=_NoConfig(), breaker=CircuitBreaker(99, 60))
        try:
            await gw5.complete(prompt=_prompt(), ctx=_ctx())
            rej = "NO ERROR (unexpected)"
        except UpstreamRejectedError:
            rej = "UpstreamRejectedError"
        print(f"[reject] {rej} after provider_calls={len(stub.REQUEST_LOG)} (no retry, no fallback)")
        return 0
    finally:
        await litellm.close_litellm_async_clients()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
