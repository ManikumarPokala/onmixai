"""Embedding port — the interface knowledge depends on (patterns.md §6).

The concrete OpenAI-compatible adapter lives in ``ai/adapters/openai_embedder.py``
(the only file importing the provider SDK). Tests use ``FakeEmbedder``. A vector's
width must equal the configured ``embedding_dimension``; a provider that returns a
different width is a configuration error, raised as ``EmbeddingDimensionError``
(non-retryable — the worker fails the document rather than storing wrong-width
vectors).
"""

from typing import Protocol

type Vector = list[float]


class EmbeddingError(Exception):
    """An embedding failure that is permanent (not worth retrying)."""


class EmbeddingDimensionError(EmbeddingError):
    """A provider returned vectors whose width != the configured dimension."""

    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(f"embedding dimension mismatch: expected {expected}, got {actual}")
        self.expected = expected
        self.actual = actual


class Embedder(Protocol):
    """Turns texts into vectors. Implementations batch, retry, and validate the
    dimension internally; callers pass all texts and receive one vector each, in
    order."""

    async def embed(self, texts: list[str]) -> list[Vector]: ...
