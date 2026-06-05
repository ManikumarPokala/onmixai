"""Parser/OCR Protocols and the immutable parsed-document representation.

A ``ParsedDocument`` is a flat, ordered list of content blocks (each tagged with
where it came from — page / sheet / slide / notes) plus the page count and the
table ratio used to pick a chunking strategy (Task 7).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ContentBlock:
    """One unit of extracted content with a provenance ref."""

    text: str
    ref: Mapping[
        str, str | int
    ]  # e.g. {"page": 3} / {"sheet": "Q1"} / {"slide": 2, "kind": "notes"}
    is_table: bool = False


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Structured output of a parser, consumed by the chunking strategies."""

    blocks: tuple[ContentBlock, ...]
    page_count: int
    table_ratio: float = field(default=0.0)


class OcrEngine(Protocol):
    """OCR for image-only (scanned) pages — one adapter, one fake for tests."""

    def image_to_text(self, image: bytes) -> str: ...


class Parser(Protocol):
    """Parses a document's bytes into a ``ParsedDocument``.

    Implementations enforce ``max_pages`` during parsing and raise
    ``ParserError`` (permanent) on malformed input; they never hang on a single
    pathological file.
    """

    def parse(self, data: bytes, *, max_pages: int) -> ParsedDocument: ...
