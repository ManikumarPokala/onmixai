"""PII redaction is decoupled from observability (Phase 6 exit): even with per-org redaction
DISABLED — so raw PII flows through the prompt to the provider — none of it reaches the trace, the
structured logs, or the audit trail. Those surfaces record metadata (request/org id, template
name+version, model, token counts, source chunk IDs, redaction COUNTS), never raw content.

This is the guarantee behind the admin PII toggle: turning redaction off changes only what the
MODEL sees, never what leaks to telemetry. Pure test — no DB.
"""

from uuid import uuid4

import structlog

from src.ai.gateway import ChatMessage, GatewayContext, RenderedPrompt
from src.ai.models import UsageFeature
from src.ai.tracing import CompletionTrace, TracingGateway, TracingPort
from tests.fakes.fake_gateway import FakeGateway

# Raw PII that would appear in the prompt when an org has disabled redaction.
_PII = ("jane@acme.com", "555-123-4567", "123-45-6789")


class _CapturingTracer(TracingPort):
    def __init__(self) -> None:
        self.traces: list[CompletionTrace] = []

    def span(self, name: str, **attrs: object):  # type: ignore[no-untyped-def]  # noqa: ARG002
        from contextlib import nullcontext

        return nullcontext()

    def record_completion(self, trace: CompletionTrace) -> None:
        self.traces.append(trace)


def _pii_prompt() -> RenderedPrompt:
    """A rendered prompt whose sources carry raw PII — i.e. redaction was OFF for this org."""
    sources = "Sources:\n[1] Reach jane@acme.com or 555-123-4567; SSN 123-45-6789."
    return RenderedPrompt(
        template_name="grounded_answer",
        template_version="1.0.0",
        messages=(ChatMessage("system", "Answer from sources."), ChatMessage("user", sources)),
        variables_hash="h",
    )


async def test_trace_and_logs_never_carry_raw_pii_even_with_redaction_off() -> None:
    chunk_ids = (uuid4(), uuid4())
    ctx = GatewayContext(
        org_id=uuid4(),
        user_id=uuid4(),
        feature=UsageFeature.CHAT,
        request_id="req-pii",
        source_chunk_ids=chunk_ids,
    )
    fake = FakeGateway()
    fake.queue_completion(text="See the contacts [1].")
    tracer = _CapturingTracer()
    gateway = TracingGateway(inner=fake, tracer=tracer)

    with structlog.testing.capture_logs() as logs:
        await gateway.complete(prompt=_pii_prompt(), ctx=ctx)

    # The provider DID receive the raw PII (redaction was off) — that is the org's content choice.
    assert any(_PII[0] in m.content for m in fake.calls[-1].prompt.messages)

    # But the trace records only metadata: assert no raw PII anywhere in its serialized attributes,
    # and that it positively recorded the source chunk IDs + template (so it is a real trace).
    assert len(tracer.traces) == 1
    attrs = tracer.traces[0].as_attributes()
    blob = repr(attrs)
    for pii in _PII:
        assert pii not in blob, f"raw PII leaked into the trace: {pii}"
    assert attrs["source_chunk_ids"] == [str(c) for c in chunk_ids]
    assert attrs["template_name"] == "grounded_answer"

    # And no captured log line carries raw PII either.
    log_blob = repr(logs)
    for pii in _PII:
        assert pii not in log_blob, f"raw PII leaked into the logs: {pii}"
