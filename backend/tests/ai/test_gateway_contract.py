"""The LLMGateway contract — invariants every implementation must satisfy. The
``gateway`` fixture is parametrized so Task 4 plugs the real litellm adapter (pointed
at the stub) into the same suite; today it runs against the fake. The fake-specific
tests below exercise scripting/recording, which only the fake provides."""

from collections.abc import Iterator
from uuid import uuid4

import pytest

from src.ai.gateway import (
    ChatMessage,
    GatewayContext,
    LLMGateway,
    ModelRef,
    RenderedPrompt,
    UpstreamUnavailableError,
)
from src.ai.models import UsageFeature
from tests.fakes.fake_gateway import FakeGateway

_MODEL = ModelRef("openai/gpt-4o-mini")


def _prompt(text: str = "hello world") -> RenderedPrompt:
    return RenderedPrompt(
        template_name="t",
        template_version="1.0.0",
        messages=(ChatMessage("system", "be brief"), ChatMessage("user", text)),
        variables_hash="abc123",
    )


def _ctx() -> GatewayContext:
    return GatewayContext(
        org_id=uuid4(), user_id=uuid4(), feature=UsageFeature.CHAT, request_id="req-1"
    )


@pytest.fixture(params=["fake"])
def gateway(request: pytest.FixtureRequest) -> Iterator[LLMGateway]:
    # Task 4 adds "litellm" here (a real adapter against llm-stub) — same assertions.
    if request.param == "fake":
        yield FakeGateway()
    else:  # pragma: no cover - added in Task 4
        raise NotImplementedError(request.param)


async def test_complete_returns_reconciling_attributed_completion(gateway: LLMGateway) -> None:
    completion = await gateway.complete(prompt=_prompt(), ctx=_ctx(), model=_MODEL)
    assert completion.text
    assert completion.total_tokens == completion.prompt_tokens + completion.completion_tokens
    assert completion.trace_id  # always present — the join key to the usage event
    assert completion.finish_reason
    assert completion.model_used == _MODEL.name


# --- fake-specific capabilities (scripting + recording) ---


async def test_fake_records_each_call_with_prompt_version_and_context() -> None:
    fake = FakeGateway()
    await fake.complete(prompt=_prompt(), ctx=_ctx(), model=ModelRef("m/x"))
    assert len(fake.calls) == 1
    assert fake.calls[0].prompt.template_version == "1.0.0"
    assert fake.calls[0].ctx.feature == UsageFeature.CHAT
    assert fake.calls[0].model == ModelRef("m/x")


async def test_fake_returns_scripted_completions_in_order() -> None:
    fake = FakeGateway()
    fake.queue_completion(text="first")
    fake.queue_completion(text="second")
    assert (await fake.complete(prompt=_prompt(), ctx=_ctx())).text == "first"
    assert (await fake.complete(prompt=_prompt(), ctx=_ctx())).text == "second"


async def test_fake_raises_scripted_error() -> None:
    fake = FakeGateway()
    fake.queue_error(UpstreamUnavailableError())
    with pytest.raises(UpstreamUnavailableError):
        await fake.complete(prompt=_prompt(), ctx=_ctx())
