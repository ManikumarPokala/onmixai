"""Tracing: every completion (success AND each typed failure) emits exactly one
complete trace; trace_id round-trips from the completion; the logging + langfuse
exporters both satisfy the port (langfuse against a fake client)."""

from typing import Any
from uuid import uuid4

import pytest

from src.ai.adapters.langfuse_tracer import LangfuseTracer
from src.ai.gateway import (
    BudgetExceededError,
    ChatMessage,
    GatewayContext,
    RenderedPrompt,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from src.ai.models import UsageFeature
from src.ai.tracing import CompletionTrace, LoggingTracer, TracingGateway, TracingPort
from tests.fakes.fake_gateway import FakeGateway

_EXPECTED_KEYS = {
    "trace_id",
    "request_id",
    "org_id",
    "feature",
    "template_name",
    "template_version",
    "model_used",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "latency_ms",
    "source_chunk_ids",
    "finish_reason",
    "error",
}


class _RecordingTracer:
    def __init__(self) -> None:
        self.traces: list[CompletionTrace] = []

    def span(self, name: str, **attrs: object) -> Any:
        from contextlib import nullcontext

        return nullcontext()

    def record_completion(self, trace: CompletionTrace) -> None:
        self.traces.append(trace)


class _FakeLangfuse:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def create_event(self, *, name: str, metadata: dict[str, Any]) -> None:
        self.events.append((name, metadata))


def _prompt() -> RenderedPrompt:
    return RenderedPrompt("grounded_answer", "1.2.0", (ChatMessage("user", "hi"),), "h")


def _ctx() -> GatewayContext:
    return GatewayContext(
        org_id=uuid4(),
        user_id=uuid4(),
        feature=UsageFeature.CHAT,
        request_id="req-1",
        source_chunk_ids=(uuid4(), uuid4()),
    )


async def test_success_emits_one_complete_trace_with_round_tripped_trace_id() -> None:
    inner = FakeGateway()
    inner.queue_completion(prompt_tokens=11, completion_tokens=7, trace_id="trace-xyz")
    tracer = _RecordingTracer()
    ctx = _ctx()
    completion = await TracingGateway(inner=inner, tracer=tracer).complete(
        prompt=_prompt(), ctx=ctx
    )

    assert len(tracer.traces) == 1
    trace = tracer.traces[0]
    assert trace.error is None
    assert trace.trace_id == completion.trace_id == "trace-xyz"  # the usage-event join key
    assert trace.prompt_tokens == 11 and trace.completion_tokens == 7
    assert trace.template_name == "grounded_answer" and trace.template_version == "1.2.0"
    assert trace.feature == UsageFeature.CHAT and trace.request_id == "req-1"
    assert trace.source_chunk_ids == ctx.source_chunk_ids
    assert trace.latency_ms >= 0
    assert set(trace.as_attributes().keys()) == _EXPECTED_KEYS  # schema complete


async def test_streaming_success_traces_once_on_stream_done() -> None:
    inner = FakeGateway()
    inner.queue_stream(["hel", "lo"], prompt_tokens=9, trace_id="trace-stream")
    tracer = _RecordingTracer()
    events = [
        event
        async for event in TracingGateway(inner=inner, tracer=tracer).complete_stream(
            prompt=_prompt(), ctx=_ctx()
        )
    ]

    assert "".join(getattr(e, "text", "") for e in events) == "hello"  # tokens passed through
    assert len(tracer.traces) == 1  # exactly one trace, on the terminal StreamDone
    trace = tracer.traces[0]
    assert trace.error is None and trace.trace_id == "trace-stream"


async def test_streaming_failure_traces_once_and_propagates() -> None:
    inner = FakeGateway()
    inner.queue_stream([], error=UpstreamUnavailableError())
    tracer = _RecordingTracer()
    with pytest.raises(UpstreamUnavailableError):
        async for _ in TracingGateway(inner=inner, tracer=tracer).complete_stream(
            prompt=_prompt(), ctx=_ctx()
        ):
            pass
    assert len(tracer.traces) == 1
    assert tracer.traces[0].error == "UpstreamUnavailableError"


@pytest.mark.parametrize(
    "error",
    [UpstreamUnavailableError(), UpstreamRejectedError(), BudgetExceededError()],
)
async def test_each_failure_class_emits_one_trace(error: Exception) -> None:
    inner = FakeGateway()
    inner.queue_error(error)
    tracer = _RecordingTracer()
    with pytest.raises(type(error)):
        await TracingGateway(inner=inner, tracer=tracer).complete(prompt=_prompt(), ctx=_ctx())
    assert len(tracer.traces) == 1
    trace = tracer.traces[0]
    assert trace.error == type(error).__name__
    assert trace.trace_id is None and trace.prompt_tokens == 0  # no completion on failure
    assert set(trace.as_attributes().keys()) == _EXPECTED_KEYS


def test_logging_tracer_satisfies_port_and_serializes() -> None:
    tracer: TracingPort = LoggingTracer()
    trace = CompletionTrace(
        request_id="r",
        org_id=uuid4(),
        feature=UsageFeature.REPORT,
        template_name="t",
        template_version="1.0.0",
        model_used="openai/gpt-4o-mini",
        prompt_tokens=3,
        completion_tokens=2,
        latency_ms=1.5,
        source_chunk_ids=(uuid4(),),
        finish_reason="stop",
        trace_id="abc",
        error=None,
    )
    tracer.record_completion(trace)  # must not raise; structlog JSON
    with tracer.span("retrieve", k=1):
        pass
    attrs = trace.as_attributes()
    assert attrs["total_tokens"] == 5 and isinstance(attrs["source_chunk_ids"], list)


def test_langfuse_tracer_satisfies_port_against_fake_client() -> None:
    client = _FakeLangfuse()
    tracer: TracingPort = LangfuseTracer(client)
    trace = CompletionTrace(
        request_id="r",
        org_id=uuid4(),
        feature=UsageFeature.CHAT,
        template_name="t",
        template_version="1.0.0",
        model_used="m",
        prompt_tokens=1,
        completion_tokens=1,
        latency_ms=2.0,
        source_chunk_ids=(),
        finish_reason="stop",
        trace_id="lf-trace",
        error=None,
    )
    tracer.record_completion(trace)
    with tracer.span("embed"):
        pass
    assert client.events[0][0] == "ai.completion"
    assert client.events[0][1]["trace_id"] == "lf-trace"
    assert client.events[1][0] == "embed" and "latency_ms" in client.events[1][1]
