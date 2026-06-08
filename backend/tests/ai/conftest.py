"""Shared AI test harness: an in-process LLM stub (so the real litellm adapter runs
against a real HTTP endpoint with fault injection) plus a Settings builder for the
gateway. Used by the resilience drills and the litellm leg of the contract suite."""

import importlib.util
import threading
from collections.abc import AsyncIterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import litellm
import pytest

from src.shared.config import Settings

# Force litellm onto the httpx transport (not aiohttp): close_litellm_async_clients()
# closes httpx clients cleanly, so no unclosed ClientSession surfaces as an unraisable
# warning in a later test under filterwarnings=error.
litellm.disable_aiohttp_transport = True
# Keep litellm fully OFFLINE in tests: no anonymous telemetry POSTs and no fetch of the
# remote model-cost map (LITELLM_LOCAL_MODEL_COST_MAP is set in the root conftest before
# litellm imports). Those background external calls aren't test traffic — left enabled they
# leak/blocked sockets that surface as unraisable ResourceWarnings under filterwarnings=error.
litellm.telemetry = False

_STUB_PATH = Path(__file__).resolve().parents[3] / "infra" / "dev" / "llm_stub.py"
_VALID_DSN = "postgresql+asyncpg://onmixai:onmixai@localhost:5432/onmixai"


def _load_stub() -> Any:
    spec = importlib.util.spec_from_file_location("llm_stub", _STUB_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_stub_module = _load_stub()


@pytest.fixture
async def llm_stub() -> AsyncIterator[SimpleNamespace]:
    _stub_module.REQUEST_LOG.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _stub_module._Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        yield SimpleNamespace(base_url=f"http://127.0.0.1:{port}/v1", module=_stub_module)
    finally:
        # Close litellm's cached async clients before the stub dies, so an unclosed
        # client can't surface as an unraisable warning in a later test.
        await litellm.close_litellm_async_clients()
        litellm.in_memory_llm_clients_cache.flush_cache()
        server.shutdown()
        server.server_close()


def llm_settings(
    base_url: str,
    *,
    default_model: str = "openai/okmodel",
    fallback_chain: list[str] | None = None,
    timeout: int = 5,
    retries: int = 1,
    threshold: int = 5,
    reset: int = 60,
) -> Settings:
    """Settings wired to the in-process stub, with fast backoff so drills run quickly."""
    return Settings(
        _env_file=None,
        env="test",
        database_url=_VALID_DSN,
        jwt_secret="x" * 40,
        storage_endpoint="http://localhost:9000",
        storage_access_key="a",
        storage_secret_key="s",
        storage_bucket="b",
        redis_url="redis://localhost:6379/0",
        embedding_dimension=8,
        llm_base_url=base_url,
        llm_api_key="test-key",
        llm_default_model=default_model,
        llm_fallback_chain=fallback_chain or [],
        llm_timeout_seconds=timeout,
        llm_max_retries=retries,
        llm_backoff_base_seconds=0.01,
        llm_backoff_max_seconds=0.05,
        llm_circuit_failure_threshold=threshold,
        llm_circuit_reset_seconds=reset,
    )


class NoModelConfig:
    """A ModelConfigReader that always returns None → platform defaults from Settings."""

    async def get(self, org_id: Any) -> None:
        return None
