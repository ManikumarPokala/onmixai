"""Search DTOs and request/response schemas (patterns.md §8).

``SearchQuery`` is the request allow-list; ``ScoredChunk`` is the internal fused
result; ``SearchResult`` is the response allow-list (never leaks org_id/embeddings).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

# --- Request ---


class SearchQuery(BaseModel):
    """A hybrid-search request. Filters narrow results inside the SQL predicate."""

    query: str = Field(min_length=1, max_length=4096)
    collection_id: UUID | None = None
    content_type: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    cursor: int = Field(default=0, ge=0)  # offset into the fused ranking
    limit: int = Field(default=10, ge=1)  # capped at search_max_results by the service


# --- Internal DTO ---


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    """A chunk after cross-arm fusion: one entry per chunk, fused RRF score."""

    chunk_id: UUID
    document_id: UUID
    collection_id: UUID
    filename: str
    content: str
    ref: Mapping[str, str | int]
    score: float


# --- Response (allow-list) ---


class SourceAttribution(BaseModel):
    document_id: UUID
    collection_id: UUID
    filename: str
    ref: dict[str, str | int]


class SearchResultItem(BaseModel):
    chunk_id: UUID
    content: str
    score: float
    source: SourceAttribution

    @classmethod
    def from_scored(cls, scored: ScoredChunk) -> "SearchResultItem":
        return cls(
            chunk_id=scored.chunk_id,
            content=scored.content,
            score=scored.score,
            source=SourceAttribution(
                document_id=scored.document_id,
                collection_id=scored.collection_id,
                filename=scored.filename,
                ref=dict(scored.ref),
            ),
        )


class SearchResult(BaseModel):
    results: list[SearchResultItem]
    next_cursor: int | None  # offset for the next page, or None when exhausted
