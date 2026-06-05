"""OpenAI-compatible embeddings adapter — the only embedding SDK import site.

Owns the cross-cutting concerns for embedding (patterns.md §6): request timeout,
batching to ``embedding_batch_size``, bounded retry with exponential backoff +
jitter on transient provider errors, and dimension validation. Works against
OpenAI or any compatible endpoint via ``base_url``.
"""

import asyncio
import secrets

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, RateLimitError

from src.ai.embedding import EmbeddingDimensionError, EmbeddingError, Vector
from src.shared.config import Settings

# Transient failures worth retrying; other provider errors propagate immediately.
_RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError)
_BACKOFF_BASE_SECONDS = 0.5


class OpenAIEmbedder:
    """Embedder backed by an OpenAI-compatible embeddings endpoint."""

    def __init__(self, settings: Settings) -> None:
        if settings.embedding_api_key is None:
            raise EmbeddingError("EMBEDDING_API_KEY must be set to embed documents")
        self._client = AsyncOpenAI(
            api_key=settings.embedding_api_key.get_secret_value(),
            base_url=settings.embedding_base_url,
            timeout=settings.embedding_timeout_seconds,
            max_retries=0,  # retry/backoff is owned here, not by the SDK
        )
        self._model = settings.embedding_model
        self._dimension = settings.embedding_dimension
        self._batch_size = settings.embedding_batch_size
        self._max_attempts = settings.embedding_max_attempts

    async def embed(self, texts: list[str]) -> list[Vector]:
        """Embed ``texts``, sub-batched to the configured size, in order.

        Time: O(texts) network-bound, O(ceil(len/batch)) provider calls. Space:
        O(texts * dimension) for the result. Raises ``EmbeddingDimensionError`` if
        a returned vector's width != the configured dimension (permanent), or
        ``EmbeddingError`` once transient retries are exhausted.
        """
        vectors: list[Vector] = []
        for start in range(0, len(texts), self._batch_size):
            vectors.extend(await self._embed_batch(texts[start : start + self._batch_size]))
        return vectors

    async def _embed_batch(self, batch: list[str]) -> list[Vector]:
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.embeddings.create(model=self._model, input=batch)
                return [self._validated(item.embedding) for item in response.data]
            except _RETRYABLE as exc:
                last_error = exc
                if attempt < self._max_attempts:
                    await asyncio.sleep(self._backoff(attempt))
        raise EmbeddingError(
            f"embedding provider unavailable after {self._max_attempts} attempts"
        ) from last_error

    def _validated(self, embedding: list[float]) -> Vector:
        if len(embedding) != self._dimension:
            raise EmbeddingDimensionError(self._dimension, len(embedding))
        return list(embedding)

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with full jitter. Time/Space: O(1)."""
        ceiling = _BACKOFF_BASE_SECONDS * 2.0 ** (attempt - 1)
        return ceiling + secrets.randbelow(1000) / 1000 * _BACKOFF_BASE_SECONDS
