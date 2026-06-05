"""Prose chunking: sentence-aware, token-capped, with fixed overlap."""

import re
from collections.abc import Mapping

from src.knowledge.chunking.base import ChunkParams, ChunkPiece, make_piece
from src.knowledge.parsing.base import ParsedDocument

# Split after sentence-ending punctuation followed by whitespace. Good enough for
# chunking (we are grouping, not doing linguistics); deterministic and dependency-free.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

# A token unit: the sentence's whitespace tokens plus the block ref it came from.
type _Unit = tuple[list[str], Mapping[str, str | int]]


class ProseChunking:
    """Greedily packs sentences into ~token_target chunks with token_overlap carry.

    No chunk exceeds ``token_target`` tokens: sentences longer than the usable
    budget are pre-split into word windows, so a flushed chunk is at most
    ``token_target`` even after the overlap carry. Consecutive chunks share their
    boundary sentences (the overlap), which preserves context across the seam.
    """

    def __init__(self, params: ChunkParams) -> None:
        self._target = max(1, params.token_target)
        self._overlap = max(0, min(params.token_overlap, self._target - 1))

    def chunk(self, parsed: ParsedDocument) -> list[ChunkPiece]:
        """Time: O(tokens) single pass over the document's words. Space: O(chunk)."""
        units = self._units(parsed)
        return self._pack(units)

    def _units(self, parsed: ParsedDocument) -> list[_Unit]:
        """Sentences as token lists, each tagged with its source ref; overlong
        sentences pre-split so a unit never exceeds the per-chunk budget."""
        budget = max(1, self._target - self._overlap)
        units: list[_Unit] = []
        for block in parsed.blocks:
            for sentence in _SENTENCE_END.split(block.text):
                tokens = sentence.split()
                if not tokens:
                    continue
                for start in range(0, len(tokens), budget):
                    units.append((tokens[start : start + budget], block.ref))
        return units

    def _pack(self, units: list[_Unit]) -> list[ChunkPiece]:
        pieces: list[ChunkPiece] = []
        current: list[_Unit] = []
        current_tokens = 0
        for tokens, ref in units:
            if current and current_tokens + len(tokens) > self._target:
                pieces.append(self._flush(current))
                current = self._overlap_tail(current)
                current_tokens = sum(len(t) for t, _ in current)
            current.append((tokens, ref))
            current_tokens += len(tokens)
        if current:
            pieces.append(self._flush(current))
        return pieces

    def _flush(self, units: list[_Unit]) -> ChunkPiece:
        content = " ".join(token for tokens, _ in units for token in tokens)
        return make_piece(content, units[0][1])

    def _overlap_tail(self, units: list[_Unit]) -> list[_Unit]:
        """Trailing units whose cumulative tokens fit within the overlap budget."""
        if self._overlap == 0:
            return []
        tail: list[_Unit] = []
        total = 0
        for tokens, ref in reversed(units):
            if total + len(tokens) > self._overlap:
                break
            tail.append((tokens, ref))
            total += len(tokens)
        tail.reverse()
        return tail
