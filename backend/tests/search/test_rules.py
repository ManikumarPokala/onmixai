"""Pure search-rule tests — RRF fusion, filter validation, paging. No I/O."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from src.knowledge.schemas import ChunkCandidate
from src.search.exceptions import InvalidSearchFilterError
from src.search.rules import build_filters, paginate, rrf_fuse
from src.search.schemas import ScoredChunk, SearchQuery

_DOC = uuid4()
_COLL = uuid4()


def _cand(chunk_id: UUID, score: float = 0.0) -> ChunkCandidate:
    return ChunkCandidate(
        chunk_id=chunk_id,
        document_id=_DOC,
        collection_id=_COLL,
        filename="f.txt",
        content=f"content-{chunk_id}",
        ref={"page": 1},
        score=score,
    )


# --- build_filters ---


def test_build_filters_passes_valid_filters() -> None:
    q = SearchQuery(query="hello", content_type="text/plain", collection_id=_COLL)
    filters = build_filters(q)
    assert filters.collection_id == _COLL and filters.content_type == "text/plain"


def test_build_filters_rejects_unknown_content_type() -> None:
    with pytest.raises(InvalidSearchFilterError) as exc:
        build_filters(SearchQuery(query="x", content_type="image/png"))
    assert exc.value.code == "INVALID_SEARCH_FILTER"


def test_build_filters_rejects_inverted_date_range() -> None:
    after = datetime(2026, 6, 5, tzinfo=UTC)
    before = datetime(2026, 6, 1, tzinfo=UTC)
    with pytest.raises(InvalidSearchFilterError):
        build_filters(SearchQuery(query="x", created_after=after, created_before=before))


def test_build_filters_allows_equal_date_bounds() -> None:
    moment = datetime(2026, 6, 5, tzinfo=UTC)
    filters = build_filters(SearchQuery(query="x", created_after=moment, created_before=moment))
    assert filters.created_after == filters.created_before == moment


# --- rrf_fuse ---


def test_rrf_ranks_chunk_found_by_both_arms_first() -> None:
    a, b, c = uuid4(), uuid4(), uuid4()
    vector_arm = [_cand(a), _cand(b)]  # a@1, b@2
    keyword_arm = [_cand(b), _cand(c)]  # b@1, c@2
    fused = rrf_fuse([vector_arm, keyword_arm], k=60)
    # b is the only chunk in both arms -> highest fused score, ranked first.
    assert fused[0].chunk_id == b
    assert {s.chunk_id for s in fused} == {a, b, c}  # union, deduped


def test_rrf_dedupes_cross_arm_duplicates() -> None:
    a = uuid4()
    fused = rrf_fuse([[_cand(a)], [_cand(a)]], k=60)
    assert len(fused) == 1 and fused[0].chunk_id == a


def test_rrf_is_deterministic_and_empty_safe() -> None:
    a, b = UUID(int=1), UUID(int=2)
    arm = [_cand(a), _cand(b)]
    once = [s.chunk_id for s in rrf_fuse([arm], k=60)]
    twice = [s.chunk_id for s in rrf_fuse([arm], k=60)]
    assert once == twice
    assert rrf_fuse([], k=60) == []
    assert rrf_fuse([[], []], k=60) == []


def test_rrf_higher_k_compresses_score_gap() -> None:
    a, b = uuid4(), uuid4()
    arm = [_cand(a), _cand(b)]
    small_k = rrf_fuse([arm], k=1)
    large_k = rrf_fuse([arm], k=1000)
    gap_small = small_k[0].score - small_k[1].score
    gap_large = large_k[0].score - large_k[1].score
    assert gap_small > gap_large  # larger k flattens the rank weighting


# --- paginate ---


def _scored(n: int) -> list[ScoredChunk]:
    return [
        ScoredChunk(
            chunk_id=UUID(int=i),
            document_id=_DOC,
            collection_id=_COLL,
            filename="f",
            content="c",
            ref={},
            score=1.0 / (i + 1),
        )
        for i in range(n)
    ]


def test_paginate_caps_limit_and_sets_next_cursor() -> None:
    items = _scored(10)
    page, nxt = paginate(items, cursor=0, limit=999, max_results=4)
    assert len(page) == 4 and nxt == 4  # limit capped at max_results


def test_paginate_exhausts_to_none() -> None:
    items = _scored(5)
    page, nxt = paginate(items, cursor=3, limit=10, max_results=10)
    assert len(page) == 2 and nxt is None


def test_paginate_empty() -> None:
    page, nxt = paginate([], cursor=0, limit=10, max_results=10)
    assert page == [] and nxt is None
