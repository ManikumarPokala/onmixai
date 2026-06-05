"""Pure document-chunking strategies (patterns.md §4). Zero I/O.

Strategy selection lives in ``knowledge/rules.py`` (``select_chunking_strategy``);
the worker builds ``ChunkParams`` from Settings, runs the chosen strategy, and maps
the resulting ``ChunkPiece`` list onto ORM ``Chunk`` rows.
"""

from src.knowledge.chunking.base import (
    ChunkingStrategy,
    ChunkParams,
    ChunkPiece,
    count_tokens,
    dedupe_by_hash,
    make_piece,
)
from src.knowledge.chunking.prose import ProseChunking
from src.knowledge.chunking.slide import SlideChunking
from src.knowledge.chunking.table import TableAwareChunking

__all__ = [
    "ChunkParams",
    "ChunkPiece",
    "ChunkingStrategy",
    "ProseChunking",
    "SlideChunking",
    "TableAwareChunking",
    "count_tokens",
    "dedupe_by_hash",
    "make_piece",
]
