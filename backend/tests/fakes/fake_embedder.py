"""Deterministic Embedder fake — hash-derived vectors of the configured width.

Same text → same vector (so re-embedding is stable), and it records how many
``embed`` calls it received (one per worker batch) so batching can be asserted.
"""

import hashlib

from src.ai.embedding import Vector


class FakeEmbedder:
    def __init__(self, dimension: int) -> None:
        self._dimension = dimension
        self.calls = 0  # embed() invocations — one per worker batch
        self.embedded = 0  # total texts embedded

    async def embed(self, texts: list[str]) -> list[Vector]:
        self.calls += 1
        self.embedded += len(texts)
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> Vector:
        values: list[float] = []
        counter = 0
        while len(values) < self._dimension:
            digest = hashlib.sha256(f"{text}:{counter}".encode()).digest()
            for offset in range(0, len(digest), 4):
                if len(values) >= self._dimension:
                    break
                values.append(int.from_bytes(digest[offset : offset + 4], "big") / 2**32)
            counter += 1
        return values
