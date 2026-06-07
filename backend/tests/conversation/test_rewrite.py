"""Follow-up query rewriting: skips the first message, uses the rewrite on success,
falls back to the raw query on every gateway failure class (never blocking), and
sanitizes/caps the rewrite before it reaches retrieval."""

from uuid import uuid4

import pytest

from src.ai.gateway import BudgetExceededError, UpstreamRejectedError, UpstreamUnavailableError
from src.ai.prompt_registry import get_prompt_registry
from src.conversation.context import HistoryTurn
from src.conversation.rewrite import RewrittenQuery, rewrite_query
from tests.fakes.fake_gateway import FakeGateway

_HISTORY = [
    HistoryTurn("user", "Tell me about the pricing tiers"),
    HistoryTurn("assistant", "There are three."),
]


async def _rewrite(
    fake: FakeGateway, history: list[HistoryTurn], raw: str, *, max_chars: int = 512
) -> RewrittenQuery:
    return await rewrite_query(
        history,
        raw,
        gateway=fake,
        registry=get_prompt_registry(),
        org_id=uuid4(),
        user_id=uuid4(),
        request_id="r",
        max_chars=max_chars,
    )


async def test_first_message_skips_rewrite_without_calling_gateway() -> None:
    fake = FakeGateway()
    result = await _rewrite(fake, [], "what is the retention period?")
    assert result.source == "raw_first_message"
    assert result.query == "what is the retention period?"
    assert len(fake.calls) == 0  # no gateway call on the first message


async def test_successful_rewrite_is_used() -> None:
    fake = FakeGateway()
    fake.queue_completion(text="standalone pricing tiers query")
    result = await _rewrite(fake, _HISTORY, "what about the second one?")
    assert result.source == "rewritten"
    assert result.query == "standalone pricing tiers query"


@pytest.mark.parametrize(
    "error", [UpstreamUnavailableError(), UpstreamRejectedError(), BudgetExceededError()]
)
async def test_falls_back_to_raw_on_every_failure_class(error: Exception) -> None:
    fake = FakeGateway()
    fake.queue_error(error)
    result = await _rewrite(fake, _HISTORY, "raw question text")
    assert result.source == "raw_fallback"
    assert result.query == "raw question text"  # never blocks; the raw query is used


async def test_rewrite_is_sanitized_and_capped() -> None:
    fake = FakeGateway()
    fake.queue_completion(text="  word " * 100)  # long + messy
    result = await _rewrite(fake, _HISTORY, "raw", max_chars=50)
    assert result.source == "rewritten"
    assert len(result.query) <= 50 and "  " not in result.query  # collapsed + capped


async def test_empty_rewrite_falls_back_to_raw() -> None:
    fake = FakeGateway()
    fake.queue_completion(text="   \n\t  ")  # sanitizes to empty
    result = await _rewrite(fake, _HISTORY, "raw question")
    assert result.source == "raw_fallback"
    assert result.query == "raw question"
