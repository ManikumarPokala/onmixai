"""Pure search rules (no I/O) — reciprocal-rank fusion, filter validation, and
paging. Each is independently unit-testable (patterns.md §4)."""

from src.knowledge.rules import SUPPORTED_CONTENT_TYPES
from src.knowledge.schemas import ChunkCandidate, RetrievalFilters
from src.search.exceptions import InvalidSearchFilterError
from src.search.schemas import ScoredChunk, SearchQuery


def build_filters(query: SearchQuery) -> RetrievalFilters:
    """Validate + normalize request filters into the knowledge retrieval VO.

    Rejects an unknown content type and a contradictory date range; the resulting
    filters narrow the candidate query in-predicate (never widen the ACL).
    Time/Space: O(1).
    """
    if query.content_type is not None and query.content_type not in SUPPORTED_CONTENT_TYPES:
        raise InvalidSearchFilterError(detail=f"unsupported content type {query.content_type}")
    if (
        query.created_after is not None
        and query.created_before is not None
        and query.created_after > query.created_before
    ):
        raise InvalidSearchFilterError(detail="created_after is later than created_before")
    return RetrievalFilters(
        collection_id=query.collection_id,
        content_type=query.content_type,
        created_after=query.created_after,
        created_before=query.created_before,
    )


def rrf_fuse(arms: list[list[ChunkCandidate]], *, k: int) -> list[ScoredChunk]:
    """Reciprocal-rank fusion of per-arm ranked candidate lists, best-first.

    Each arm contributes ``1 / (k + rank)`` (1-based rank) to a chunk's score;
    a chunk surfaced by multiple arms sums its contributions (so fusion also
    de-duplicates). Ties break by chunk_id for determinism. Time: O(total
    candidates). Space: O(distinct chunks).
    """
    scores: dict[object, float] = {}
    seen: dict[object, ChunkCandidate] = {}
    for arm in arms:
        for rank, candidate in enumerate(arm, start=1):
            scores[candidate.chunk_id] = scores.get(candidate.chunk_id, 0.0) + 1.0 / (k + rank)
            seen.setdefault(candidate.chunk_id, candidate)
    ordered = sorted(seen.values(), key=lambda c: (-scores[c.chunk_id], str(c.chunk_id)))
    return [
        ScoredChunk(
            chunk_id=c.chunk_id,
            document_id=c.document_id,
            collection_id=c.collection_id,
            filename=c.filename,
            content=c.content,
            ref=c.ref,
            score=scores[c.chunk_id],
        )
        for c in ordered
    ]


def paginate(
    items: list[ScoredChunk], *, cursor: int, limit: int, max_results: int
) -> tuple[list[ScoredChunk], int | None]:
    """Return one page and the next cursor (offset into the fused ranking).

    ``limit`` is capped at ``max_results`` (hard server-side cap). ``next_cursor``
    is None once the ranking is exhausted. Time/Space: O(limit).
    """
    capped = min(limit, max_results)
    page = items[cursor : cursor + capped]
    next_cursor = cursor + capped if cursor + capped < len(items) else None
    return page, next_cursor
