"""Chunking primitives shared by every strategy — pure, zero I/O.

A ``ChunkPiece`` is the strategy output: normalized content, a deterministic
content hash, a token count, and a provenance ref. The worker maps pieces onto
ORM ``Chunk`` rows (assigning ``seq``). Token counts use a whitespace word model
(``len(text.split())``) — the same model the upload/ingest path already uses; a
real tokenizer is a Phase-2 concern and would slot in behind ``count_tokens``.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from src.knowledge.parsing.base import ParsedDocument


@dataclass(frozen=True, slots=True)
class ChunkParams:
    """Tunable chunking targets (from Settings) — passed in, never read here."""

    token_target: int
    token_overlap: int
    table_rows: int


@dataclass(frozen=True, slots=True)
class ChunkPiece:
    """One chunk produced by a strategy, before it becomes an ORM row."""

    content: str
    content_hash: str
    token_count: int
    metadata: Mapping[str, str | int]


class ChunkingStrategy(Protocol):
    """Turns a parsed document into ordered chunk pieces. Pure, deterministic."""

    def chunk(self, parsed: ParsedDocument) -> list[ChunkPiece]: ...


def count_tokens(text: str) -> int:
    """Whitespace-token count. Time: O(len(text)). Space: O(tokens)."""
    return len(text.split())


def make_piece(content: str, metadata: Mapping[str, str | int]) -> ChunkPiece:
    """Build a piece with a whitespace-normalized content hash.

    Normalizing collapses incidental whitespace so trivial formatting differences
    do not change the hash — re-ingesting the same document yields identical hashes
    (idempotency). Time: O(len(content)). Space: O(len(content)).
    """
    normalized = " ".join(content.split())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return ChunkPiece(
        content=content,
        content_hash=digest,
        token_count=len(normalized.split()),
        metadata=metadata,
    )


def dedupe_by_hash(pieces: list[ChunkPiece]) -> list[ChunkPiece]:
    """Drop later pieces whose content hash already appeared (preserve order).

    The ``(document_id, content_hash)`` uniqueness constraint forbids storing the
    same content twice; de-duping here keeps that invariant deterministic.
    Time: O(n) over pieces. Space: O(n) for the seen-set.
    """
    seen: set[str] = set()
    unique: list[ChunkPiece] = []
    for piece in pieces:
        if piece.content_hash in seen:
            continue
        seen.add(piece.content_hash)
        unique.append(piece)
    return unique
