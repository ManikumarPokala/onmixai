"""GroundedAnswerPipeline scripted-fake matrix — the cite-or-refuse invariant proven
across every branch: happy path (validated citations), zero-marker → ungrounded refusal,
phantom-marker stripping, low-confidence refusal BEFORE generation (zero completion
calls), retrieval-empty refusal, generation-failure refusal, and rewrite integration.
"""

from uuid import uuid4

import pytest

from src.ai.gateway import UpstreamUnavailableError
from src.ai.guardrails import Refusal
from src.ai.prompt_registry import get_prompt_registry
from src.conversation.context import HistoryTurn
from src.conversation.pipeline import AnsweredTurn, GroundedAnswerPipeline, PipelineOutcome
from src.identity.models import Role
from src.identity.schemas import AuthContext
from src.search.schemas import SearchQuery, SearchResult, SearchResultItem, SourceAttribution
from tests.ai.conftest import llm_settings
from tests.fakes.fake_gateway import FakeGateway


def _item(content: str, score: float) -> SearchResultItem:
    return SearchResultItem(
        chunk_id=uuid4(),
        content=content,
        score=score,
        source=SourceAttribution(
            document_id=uuid4(), collection_id=uuid4(), filename="doc.txt", ref={"page": 3}
        ),
    )


class _FakeRetriever:
    def __init__(self, items: list[SearchResultItem]) -> None:
        self._items = items
        self.last_query: str | None = None

    async def search(self, actor: AuthContext, query: SearchQuery) -> SearchResult:
        self.last_query = query.query
        return SearchResult(results=self._items, next_cursor=None)


def _actor() -> AuthContext:
    return AuthContext(user_id=uuid4(), org_id=uuid4(), role=Role.MEMBER)


def _pipeline(retriever: _FakeRetriever, gateway: FakeGateway) -> GroundedAnswerPipeline:
    return GroundedAnswerPipeline(
        retriever=retriever,
        gateway=gateway,
        registry=get_prompt_registry(),
        settings=llm_settings("http://x"),
    )


async def _answer(
    pipeline: GroundedAnswerPipeline, *, history: list[HistoryTurn] | None = None
) -> PipelineOutcome:
    return await pipeline.answer(
        actor=_actor(),
        raw_query="what is the retention period?",
        history=history or [],
        summary=None,
        request_id="req-1",
    )


async def test_happy_path_validated_citations_reference_retrieved_chunks() -> None:
    items = [_item("retention is 90 days", 0.9), _item("purge runs nightly", 0.8)]
    fake = FakeGateway()
    fake.queue_completion(text="Retention is 90 days [1]; purge is nightly [2].")
    outcome = await _answer(_pipeline(_FakeRetriever(items), fake))

    assert isinstance(outcome, AnsweredTurn)
    assert "[1]" in outcome.content and "[2]" in outcome.content
    assert len(outcome.citations) == 2
    retrieved_ids = {item.chunk_id for item in items}
    assert all(c.chunk_id in retrieved_ids for c in outcome.citations)  # only retrieved chunks
    assert outcome.prompt_version == "1.1.0" and outcome.model_used and outcome.trace_id
    assert len(fake.calls) == 1  # only the generation call (rewrite skipped on first turn)


async def test_zero_marker_answer_is_ungrounded_refusal() -> None:
    fake = FakeGateway()
    fake.queue_completion(text="Retention is ninety days, purge is nightly.")  # no markers
    outcome = await _answer(_pipeline(_FakeRetriever([_item("x", 0.9)]), fake))
    assert outcome == Refusal("UNGROUNDED_ANSWER")


async def test_minority_phantom_marker_is_stripped_valid_survives() -> None:
    items = [_item("a", 0.9), _item("b", 0.8)]
    fake = FakeGateway()
    fake.queue_completion(text="Claim [1] and claim [2] but a phantom [9].")  # 1 of 3 phantom
    outcome = await _answer(_pipeline(_FakeRetriever(items), fake))
    assert isinstance(outcome, AnsweredTurn)
    assert "[9]" not in outcome.content and "[1]" in outcome.content and "[2]" in outcome.content
    assert len(outcome.citations) == 2


async def test_majority_fabricated_citations_are_refused() -> None:
    # phantom at parity with real (1 of 2) → refuse rather than serve a half-fabricated set
    items = [_item("a", 0.9), _item("b", 0.8)]
    fake = FakeGateway()
    fake.queue_completion(text="Real [1] but fabricated [9].")
    outcome = await _answer(_pipeline(_FakeRetriever(items), fake))
    assert outcome == Refusal("UNGROUNDED_ANSWER")


async def test_all_phantom_markers_is_refusal() -> None:
    fake = FakeGateway()
    fake.queue_completion(text="Per [9] and [7] the answer is yes.")  # both phantom (2 sources)
    outcome = await _answer(_pipeline(_FakeRetriever([_item("a", 0.9), _item("b", 0.8)]), fake))
    assert outcome == Refusal("UNGROUNDED_ANSWER")


async def test_low_confidence_refuses_before_generation_with_zero_completion_calls() -> None:
    fake = FakeGateway()
    fake.queue_completion(text="should never be used [1]")
    outcome = await _answer(_pipeline(_FakeRetriever([]), fake))  # zero retrieved
    assert outcome == Refusal("INSUFFICIENT_SOURCES")
    assert len(fake.calls) == 0  # refused BEFORE any generation spend


async def test_generation_failure_propagates_as_error_not_refusal() -> None:
    # An infrastructure failure is NOT a content refusal — it propagates (the SSE layer
    # turns it into an error event; no assistant row; re-askable).
    fake = FakeGateway()
    fake.queue_error(UpstreamUnavailableError())
    with pytest.raises(UpstreamUnavailableError):
        await _answer(_pipeline(_FakeRetriever([_item("a", 0.9)]), fake))


async def test_rewrite_result_feeds_retrieval() -> None:
    retriever = _FakeRetriever([_item("a", 0.9)])
    fake = FakeGateway()
    fake.queue_completion(text="standalone rewritten query")  # consumed by the rewrite step
    fake.queue_completion(text="Answer [1].")  # consumed by generation
    history = [HistoryTurn("user", "tell me about retention"), HistoryTurn("assistant", "ok")]
    outcome = await pipeline_answer(retriever, fake, history)
    assert isinstance(outcome, AnsweredTurn)
    assert retriever.last_query == "standalone rewritten query"  # rewrite fed retrieval


async def pipeline_answer(
    retriever: _FakeRetriever, fake: FakeGateway, history: list[HistoryTurn]
) -> PipelineOutcome:
    return await _answer(_pipeline(retriever, fake), history=history)


def _sources_text(fake: FakeGateway) -> str:
    """The concatenated rendered-prompt message text of the last gateway call."""
    return "\n".join(m.content for m in fake.calls[-1].prompt.messages)


async def test_pii_redaction_toggle_governs_grounding_sources() -> None:
    # The same retrieved source, with redaction ON then OFF — the toggle controls only what the
    # model sees in the prompt (Task 9). Decoupling from telemetry is proven separately (Task 10).
    pii_item = [_item("Contact jane@acme.com or 555-123-4567 about retention.", 0.9)]
    on = FakeGateway()
    on.queue_completion(text="See the contact details [1].")
    await _pipeline(_FakeRetriever(pii_item), on).answer(
        actor=_actor(),
        raw_query="who do I contact?",
        history=[],
        summary=None,
        request_id="r",
        redact_pii=True,
    )
    text_on = _sources_text(on)
    assert "jane@acme.com" not in text_on and "[REDACTED_EMAIL]" in text_on

    off = FakeGateway()
    off.queue_completion(text="See the contact details [1].")
    await _pipeline(_FakeRetriever(pii_item), off).answer(
        actor=_actor(),
        raw_query="who do I contact?",
        history=[],
        summary=None,
        request_id="r",
        redact_pii=False,
    )
    text_off = _sources_text(off)
    assert "jane@acme.com" in text_off  # redaction off → raw content reaches the model
