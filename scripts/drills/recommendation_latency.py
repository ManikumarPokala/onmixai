#!/usr/bin/env python
"""Recommendation latency drill (Phase 5, Task 9).

Measures the latency of the recommendation pipeline's hot path: retrieve → confidence band →
decline gate → ONE blocking structured generation → justification grounding. Unlike chat, a
recommendation is a SINGLE non-streaming structured call, so the wall-clock is dominated by
that one ``gateway.complete`` round-trip; retrieval is a fixed in-memory source (the real
hybrid /search hot path is benchmarked separately, ADR 0009) and the band/grounding work is
pure and O(j·m), negligible.

Generation is served by the in-process llm_stub with an injected per-call delay modeling a
mid-size hosted LLM's structured completion:

    STUB_JSON_MS  — modeled time for one blocking JSON completion (default 600 ms)

This is harness + mechanics correctness, NOT real-model quality or speed — real-provider
numbers are re-measured when a provider is configured (revisit trigger). Reports p50/p95 and
enforces a generous mechanics budget (p95 < 10 s) so a regression in the path (e.g. an
accidental extra round-trip) is caught.

Run from the repo root:
    bash scripts/drills/recommendation_latency.sh
or directly:
    STUB_JSON_MS=600 backend/.venv/bin/python scripts/drills/recommendation_latency.py
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
from src.identity.models import Role
from src.identity.schemas import AuthContext
from src.recommendation.pipeline import CompletedRecommendation, RecommendationPipeline
from src.search.schemas import SearchQuery, SearchResult, SearchResultItem, SourceAttribution
from src.shared.config import Settings

_N = int(os.environ.get("RECOMMENDATION_LATENCY_N", "100"))
_STUB_PATH = (Path(__file__).resolve().parents[2] / "infra" / "dev" / "llm_stub.py").resolve()
_P95_BUDGET_MS = 10_000


class _NoConfig:
    async def get(self, org_id: Any) -> None:
        return None


class _FixedRetriever:
    """Two constant in-memory sources whose summed score clears the confidence floor, so every
    turn produces a completed (not declined) recommendation citing source [1]."""

    def __init__(self) -> None:
        self._items = [
            SearchResultItem(
                chunk_id=uuid4(),
                content=f"Evidence fragment {i + 1} supporting the decision.",
                score=0.08,
                source=SourceAttribution(
                    document_id=uuid4(),
                    collection_id=uuid4(),
                    filename="corpus.txt",
                    ref={"page": i + 1},
                ),
            )
            for i in range(2)
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
    spec = importlib.util.spec_from_file_location("llm_stub_rec_drill", _STUB_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
    return ordered[rank]


async def _measure(base_url: str) -> list[float]:
    settings = _settings(base_url)
    gateway = LiteLLMGateway(settings=settings, configs=_NoConfig(), breaker=CircuitBreaker(5, 60))
    pipeline = RecommendationPipeline(
        retriever=_FixedRetriever(),
        gateway=gateway,
        registry=get_prompt_registry(),
        settings=settings,
    )
    actor = AuthContext(user_id=uuid4(), org_id=uuid4(), role=Role.MEMBER)
    latencies: list[float] = []
    try:
        for i in range(_N):
            start = time.perf_counter()
            outcome = await pipeline.recommend(
                actor=actor,
                query=f"Which option should we choose? (turn {i})",
                collection_scope=[],
                request_id="rec-latency",
            )
            latencies.append((time.perf_counter() - start) * 1000)
            assert isinstance(outcome, CompletedRecommendation)  # sanity: not declined
    finally:
        await litellm.close_litellm_async_clients()
    return latencies


def main() -> int:
    json_ms = os.environ.get("STUB_JSON_MS", "600")
    os.environ.setdefault("STUB_JSON_MS", json_ms)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _load_stub()._Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    try:
        latencies = asyncio.run(_measure(base_url))
    finally:
        server.shutdown()
        server.server_close()

    p50, p95 = _percentile(latencies, 50), _percentile(latencies, 95)
    print(
        f"\n[recommendation latency] n={_N} delay_model(json={json_ms}ms per structured call)\n"
        f"  end-to-end  p50={p50:7.1f}ms  p95={p95:7.1f}ms\n"
        f"  (single structured call dominates; stub delay model — real provider re-measured later)"
    )

    ok = p95 < _P95_BUDGET_MS
    if not ok:
        print(f"  BUDGET FAIL: p95 < {_P95_BUDGET_MS}ms required (mechanics regression guard)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
