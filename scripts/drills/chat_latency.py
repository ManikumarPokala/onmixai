#!/usr/bin/env python
"""Chat streaming latency drill (Phase 4, Task 9).

Measures first-token and full-response latency of the grounded chat pipeline's STREAMING
path (rewrite → retrieve → assemble → ``complete_stream``) from the SSE-event side — the hot
path that produces ``token`` events. Generation is served by the in-process llm_stub with an
injected, OpenAI-style streaming delay model:

    STUB_STREAM_FIRST_MS  — time to the first token   (default 400 ms)
    STUB_STREAM_TOKEN_MS  — time per subsequent token (default 25 ms)

These model a mid-size hosted LLM. Retrieval is a fixed in-memory source (the real retrieval
hot path is benchmarked separately — hybrid /search p95 < 3s @ 100k, ADR 0009 — and context
assembly is budgeted < 50 ms, §8), so this drill isolates the generation-streaming component,
which dominates first-token and scales the full response with answer length. Real-provider
numbers are re-measured when a provider is configured (revisit trigger).

Run from the repo root:
    bash scripts/drills/chat_latency.sh
or directly:
    STUB_STREAM_FIRST_MS=400 STUB_STREAM_TOKEN_MS=25 \
      backend/.venv/bin/python scripts/drills/chat_latency.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

import litellm

from src.ai.adapters.circuit_breaker import CircuitBreaker
from src.ai.adapters.litellm_gateway import LiteLLMGateway
from src.ai.prompt_registry import get_prompt_registry
from src.conversation.pipeline import GroundedAnswerPipeline, TokenChunk
from src.identity.models import Role
from src.identity.schemas import AuthContext
from src.search.schemas import SearchQuery, SearchResult, SearchResultItem, SourceAttribution
from src.shared.config import Settings

_N = int(os.environ.get("CHAT_LATENCY_N", "100"))
_STUB_PATH = (Path(__file__).resolve().parents[2] / "infra" / "dev" / "llm_stub.py").resolve()
_FIRST_TOKEN_BUDGET_MS = 3_000
_FULL_P95_BUDGET_MS = 15_000


class _NoConfig:
    """A ModelConfigReader that always returns None → platform defaults from Settings."""

    async def get(self, org_id: Any) -> None:
        return None


class _FixedRetriever:
    """A constant in-memory source that supports the drill question (shares the token the stub
    grounds on), so every turn yields a streamed grounded answer."""

    def __init__(self) -> None:
        self._items = [
            SearchResultItem(
                chunk_id=uuid4(),
                content="A Zarvon is a small tool kept in a shed near the bay.",
                score=0.9,
                source=SourceAttribution(
                    document_id=uuid4(),
                    collection_id=uuid4(),
                    filename="corpus.txt",
                    ref={"page": 1},
                ),
            )
        ]

    async def search(self, actor: AuthContext, query: SearchQuery) -> SearchResult:
        return SearchResult(results=self._items, next_cursor=None)


def _settings(base_url: str) -> Settings:
    return Settings(
        _env_file=None,
        env="test",
        database_url="postgresql+asyncpg://u:p@localhost:5432/db",
        jwt_secret="x" * 40,
        storage_endpoint="http://localhost:9000",
        storage_access_key="a",
        storage_secret_key="s",
        storage_bucket="b",
        redis_url="redis://localhost:6379/0",
        embedding_dimension=8,
        llm_base_url=base_url,
        llm_api_key="drill-key",
        llm_default_model="openai/stub",
        llm_timeout_seconds=30,
    )


def _load_stub() -> Any:
    spec = importlib.util.spec_from_file_location("llm_stub_drill", _STUB_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
    return ordered[rank]


async def _measure(base_url: str) -> tuple[list[float], list[float], int]:
    settings = _settings(base_url)
    gateway = LiteLLMGateway(
        settings=settings, configs=_NoConfig(), breaker=CircuitBreaker(5, 60)
    )
    pipeline = GroundedAnswerPipeline(
        retriever=_FixedRetriever(),
        gateway=gateway,
        registry=get_prompt_registry(),
        settings=settings,
    )
    actor = AuthContext(user_id=uuid4(), org_id=uuid4(), role=Role.MEMBER)
    first_ms: list[float] = []
    full_ms: list[float] = []
    answer_tokens = 0
    try:
        for i in range(_N):
            start = time.perf_counter()
            first: float | None = None
            tokens = 0
            async for event in pipeline.answer_stream(
                actor=actor,
                raw_query=f"What is a Zarvon used for? (turn {i})",
                history=[],
                summary=None,
                request_id="chat-latency",
            ):
                if isinstance(event, TokenChunk):
                    tokens += 1
                    if first is None:
                        first = time.perf_counter() - start
            full = time.perf_counter() - start
            first_ms.append((first if first is not None else full) * 1000)
            full_ms.append(full * 1000)
            answer_tokens = tokens
    finally:
        await litellm.close_litellm_async_clients()
    return first_ms, full_ms, answer_tokens


def main() -> int:
    first_delay = os.environ.get("STUB_STREAM_FIRST_MS", "400")
    token_delay = os.environ.get("STUB_STREAM_TOKEN_MS", "25")
    os.environ.setdefault("STUB_STREAM_FIRST_MS", first_delay)
    os.environ.setdefault("STUB_STREAM_TOKEN_MS", token_delay)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _load_stub()._Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    try:
        first_ms, full_ms, answer_tokens = asyncio.run(_measure(base_url))
    finally:
        server.shutdown()
        server.server_close()

    ft_p50, ft_p95 = _percentile(first_ms, 50), _percentile(first_ms, 95)
    full_p50, full_p95 = _percentile(full_ms, 50), _percentile(full_ms, 95)
    print(
        f"\n[chat latency] n={_N} answer_tokens≈{answer_tokens} "
        f"delay_model(first={first_delay}ms, per_token={token_delay}ms)\n"
        f"  first-token  p50={ft_p50:7.1f}ms  p95={ft_p95:7.1f}ms\n"
        f"  full         p50={full_p50:7.1f}ms  p95={full_p95:7.1f}ms\n"
        f"  (stub delay model — generation-streaming component; real provider re-measured later)"
    )

    ok = ft_p95 < _FIRST_TOKEN_BUDGET_MS and full_p95 < _FULL_P95_BUDGET_MS
    if not ok:
        print(
            f"  BUDGET FAIL: first-token p95 < {_FIRST_TOKEN_BUDGET_MS}ms and "
            f"full p95 < {_FULL_P95_BUDGET_MS}ms required"
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
