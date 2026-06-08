"""RecommendationPipeline scripted-fake matrix — single-model, grounded, decline-or-cite.

Proves: a completed recommendation with the band attached FROM RETRIEVAL SCORES and NOT from
anything the model claims about itself (the PRD interview-trap); a below-floor/empty decline
BEFORE any generation spend; phantom-marker stripping; a justification emptied → decline; a
persistently schema-invalid output → typed error (not silent); an infrastructure failure →
typed error (not a decline), nothing produced.
"""

import json
from uuid import UUID, uuid4

import pytest

from src.ai.gateway import UpstreamRejectedError, UpstreamUnavailableError
from src.ai.prompt_registry import get_prompt_registry
from src.identity.models import Role
from src.identity.schemas import AuthContext
from src.recommendation.pipeline import (
    CompletedRecommendation,
    Declined,
    RecommendationOutcome,
    RecommendationPipeline,
)
from src.search.schemas import SearchQuery, SearchResult, SearchResultItem, SourceAttribution
from tests.ai.conftest import llm_settings
from tests.fakes.fake_gateway import FakeGateway


def _item(score: float, content: str = "source text") -> SearchResultItem:
    return SearchResultItem(
        chunk_id=uuid4(),
        content=content,
        score=score,
        source=SourceAttribution(
            document_id=uuid4(), collection_id=uuid4(), filename="doc.txt", ref={"page": 2}
        ),
    )


class _FakeRetriever:
    def __init__(self, items: list[SearchResultItem]) -> None:
        self._items = items

    async def search(self, actor: AuthContext, query: SearchQuery) -> SearchResult:
        return SearchResult(results=self._items, next_cursor=None)


def _actor() -> AuthContext:
    return AuthContext(user_id=uuid4(), org_id=uuid4(), role=Role.MEMBER)


def _pipeline(retriever: _FakeRetriever, gateway: FakeGateway) -> RecommendationPipeline:
    return RecommendationPipeline(
        retriever=retriever,
        gateway=gateway,
        registry=get_prompt_registry(),
        settings=llm_settings("http://x"),
    )


def _output_json(markers: list[int], *, recommendation: str = "Choose A.") -> str:
    return json.dumps(
        {
            "recommendation": recommendation,
            "alternatives": [{"option": "B", "rationale": "cheaper"}],
            "justifications": [{"claim": "A has the better SLA", "citation_markers": markers}],
            "caveats": ["limited data"],
        }
    )


async def _recommend(
    pipeline: RecommendationPipeline, scope: list[UUID] | None = None
) -> RecommendationOutcome:
    return await pipeline.recommend(
        actor=_actor(), query="which vendor?", collection_scope=scope or [], request_id="r"
    )


async def test_band_is_derived_from_scores_not_the_model_claim() -> None:
    # SAME model output (whose text even claims "99% certain"); only the retrieval scores differ.
    claim = "Choose A. I am 99% certain and fully confident."

    high_gateway = FakeGateway()
    high_gateway.queue_completion(text=_output_json([1], recommendation=claim))
    high = await _recommend(
        _pipeline(_FakeRetriever([_item(0.06), _item(0.06), _item(0.06)]), high_gateway)
    )
    assert isinstance(high, CompletedRecommendation)
    assert high.band == "high"  # sum 0.18 ≥ high threshold

    low_gateway = FakeGateway()
    low_gateway.queue_completion(text=_output_json([1], recommendation=claim))
    low = await _recommend(_pipeline(_FakeRetriever([_item(0.04)]), low_gateway))
    assert isinstance(low, CompletedRecommendation)
    assert low.band == "low"  # sum 0.04 → low — the model's "99% certain" did NOT set it


async def test_below_floor_declines_before_generation() -> None:
    gateway = FakeGateway()
    result = await _recommend(_pipeline(_FakeRetriever([_item(0.01)]), gateway))  # sum < floor
    assert isinstance(result, Declined) and result.reason == "INSUFFICIENT_EVIDENCE"
    assert gateway.calls == []  # zero generation spend


async def test_empty_retrieval_declines_before_generation() -> None:
    gateway = FakeGateway()
    result = await _recommend(_pipeline(_FakeRetriever([]), gateway))
    assert isinstance(result, Declined) and result.reason == "INSUFFICIENT_EVIDENCE"
    assert gateway.calls == []


async def test_phantom_marker_is_stripped() -> None:
    gateway = FakeGateway()
    gateway.queue_completion(text=_output_json([1, 5]))  # only 1 source → 5 is phantom
    result = await _recommend(_pipeline(_FakeRetriever([_item(0.06), _item(0.06)]), gateway))
    assert isinstance(result, CompletedRecommendation)
    assert result.output.justifications[0].citation_markers == [1]  # 5 stripped
    assert [c.marker_index for c in result.citations] == [1]


async def test_justification_emptied_declines() -> None:
    gateway = FakeGateway()
    gateway.queue_completion(text=_output_json([5]))  # only phantom marker → claim unsupported
    result = await _recommend(_pipeline(_FakeRetriever([_item(0.06)]), gateway))
    assert isinstance(result, Declined) and result.reason == "INSUFFICIENT_EVIDENCE"


async def test_schema_invalid_output_propagates_typed_error() -> None:
    # The gateway exhausts its bounded re-ask and raises a typed rejection (Phase 3); the
    # pipeline must NOT swallow it into a decline or a fabricated default.
    gateway = FakeGateway()
    gateway.queue_error(UpstreamRejectedError("SCHEMA_VALIDATION_FAILED"))
    with pytest.raises(UpstreamRejectedError):
        await _recommend(_pipeline(_FakeRetriever([_item(0.06)]), gateway))


async def test_infrastructure_failure_propagates_not_declines() -> None:
    gateway = FakeGateway()
    gateway.queue_error(UpstreamUnavailableError())
    with pytest.raises(UpstreamUnavailableError):  # an outage is NOT a decline
        await _recommend(_pipeline(_FakeRetriever([_item(0.06)]), gateway))
