"""Embedder tests: the fake satisfies the contract; the OpenAI adapter batches,
retries transient failures, and rejects a wrong-dimension response. No network —
the adapter's SDK client is stubbed."""

from typing import Any

import httpx
import pytest
from openai import APIConnectionError

from src.ai.adapters.openai_embedder import OpenAIEmbedder
from src.ai.embedding import Embedder, EmbeddingDimensionError, EmbeddingError, Vector
from src.shared.config import Settings
from tests.fakes.fake_embedder import FakeEmbedder

_DIMENSION = 16


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "env": "test",
        "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
        "jwt_secret": "test-secret-key-at-least-32-characters-long",
        "storage_endpoint": "http://localhost:9000",
        "storage_access_key": "a",
        "storage_secret_key": "b",
        "storage_bucket": "bk",
        "redis_url": "redis://localhost:6379/0",
        "embedding_dimension": _DIMENSION,
        "embedding_api_key": "sk-test",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


# --- a stub OpenAI client (the adapter only touches .embeddings.create / .data) ---


class _Item:
    def __init__(self, embedding: list[float]) -> None:
        self.embedding = embedding


class _Response:
    def __init__(self, data: list[_Item]) -> None:
        self.data = data


class _Embeddings:
    def __init__(self, vector_for: Any, fail_times: int = 0) -> None:
        self._vector_for = vector_for
        self._fail_times = fail_times
        self.batches: list[list[str]] = []

    async def create(self, *, model: str, input: list[str]) -> _Response:
        self.batches.append(list(input))
        if len(self.batches) <= self._fail_times:
            raise APIConnectionError(request=httpx.Request("POST", "http://stub"))
        return _Response([_Item(self._vector_for(text)) for text in input])


class _StubClient:
    def __init__(self, vector_for: Any, fail_times: int = 0) -> None:
        self.embeddings = _Embeddings(vector_for, fail_times)


# --- contract suite (run against the fake) ---


async def _assert_embedder_contract(embedder: Embedder, dimension: int) -> None:
    texts = ["alpha", "beta", "gamma"]
    vectors = await embedder.embed(texts)
    assert len(vectors) == len(texts)
    assert all(len(vector) == dimension for vector in vectors)
    assert await embedder.embed(texts) == vectors  # deterministic
    assert await embedder.embed([]) == []  # empty input → empty output


async def test_fake_embedder_satisfies_contract() -> None:
    await _assert_embedder_contract(FakeEmbedder(_DIMENSION), _DIMENSION)


def test_fake_is_deterministic_across_instances() -> None:
    one = FakeEmbedder(_DIMENSION)._vector("hello")
    two = FakeEmbedder(_DIMENSION)._vector("hello")
    assert one == two and len(one) == _DIMENSION


async def test_openai_adapter_satisfies_contract() -> None:
    embedder = OpenAIEmbedder(_settings())
    embedder._client = _StubClient(lambda _text: [0.1] * _DIMENSION)  # type: ignore[assignment]
    await _assert_embedder_contract(embedder, _DIMENSION)


async def test_openai_adapter_batches_to_configured_size() -> None:
    embedder = OpenAIEmbedder(_settings(embedding_batch_size=2))
    stub = _StubClient(lambda _text: [0.0] * _DIMENSION)
    embedder._client = stub  # type: ignore[assignment]
    await embedder.embed([f"t{i}" for i in range(5)])
    assert len(stub.embeddings.batches) == 3  # ceil(5 / 2)
    assert all(len(batch) <= 2 for batch in stub.embeddings.batches)


async def test_openai_adapter_retries_transient_failure() -> None:
    embedder = OpenAIEmbedder(_settings(embedding_max_attempts=3))
    stub = _StubClient(lambda _text: [0.0] * _DIMENSION, fail_times=1)
    embedder._client = stub  # type: ignore[assignment]
    embedder._backoff = lambda _attempt: 0.0  # type: ignore[method-assign]
    vectors = await embedder.embed(["x"])
    assert len(vectors) == 1
    assert len(stub.embeddings.batches) == 2  # failed once, then succeeded


async def test_openai_adapter_rejects_wrong_dimension() -> None:
    embedder = OpenAIEmbedder(_settings(embedding_dimension=_DIMENSION))
    embedder._client = _StubClient(lambda _text: [0.0] * (_DIMENSION - 1))  # type: ignore[assignment]
    with pytest.raises(EmbeddingDimensionError) as exc:
        await embedder.embed(["x"])
    assert "dimension mismatch" in str(exc.value)
    assert exc.value.expected == _DIMENSION and exc.value.actual == _DIMENSION - 1


def test_openai_adapter_requires_api_key() -> None:
    with pytest.raises(EmbeddingError) as exc:
        OpenAIEmbedder(_settings(embedding_api_key=None))
    assert "EMBEDDING_API_KEY" in str(exc.value)


def test_fake_returns_unit_interval_components() -> None:
    vector: Vector = FakeEmbedder(_DIMENSION)._vector("anything")
    assert all(0.0 <= value < 1.0 for value in vector)
