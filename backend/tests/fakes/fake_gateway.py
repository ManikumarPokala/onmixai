"""Scriptable LLMGateway fake — the instrument every Phase 4–5 test uses in place of
a provider. Queue per-call completions / errors / latencies, and inspect every
recorded call (prompt + version, context, model). Same-input → same default output,
so tests are deterministic without a network."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from pydantic import BaseModel

from src.ai.gateway import (
    Completion,
    GatewayContext,
    ModelRef,
    RenderedPrompt,
    StreamDone,
    StreamEvent,
    TextDelta,
)


@dataclass(frozen=True)
class RecordedCall:
    prompt: RenderedPrompt
    ctx: GatewayContext
    model: ModelRef | None
    response_schema: type[BaseModel] | None


@dataclass
class _Outcome:
    result: Completion | Exception
    delay_s: float


@dataclass
class _StreamScript:
    tokens: list[str]
    completion: Completion
    error: Exception | None  # raised before the first token, if set
    cancelled: bool = field(default=False)  # set if the consumer cancelled mid-stream


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[RecordedCall] = []
        self.stream_calls: list[RecordedCall] = []
        self._script: list[_Outcome] = []
        self._stream_script: list[_StreamScript] = []
        self._counter = 0

    def queue_completion(
        self,
        *,
        text: str = "fake completion",
        model_used: str = "fake/model",
        prompt_tokens: int = 10,
        completion_tokens: int = 5,
        finish_reason: str = "stop",
        trace_id: str | None = None,
        delay_s: float = 0.0,
    ) -> None:
        completion = Completion(
            text=text,
            model_used=model_used,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason=finish_reason,
            trace_id=trace_id or f"fake-trace-{len(self._script)}",
        )
        self._script.append(_Outcome(completion, delay_s))

    def queue_error(self, error: Exception, *, delay_s: float = 0.0) -> None:
        self._script.append(_Outcome(error, delay_s))

    def queue_stream(
        self,
        tokens: list[str],
        *,
        model_used: str = "fake/model",
        prompt_tokens: int = 10,
        trace_id: str | None = None,
        error: Exception | None = None,
    ) -> _StreamScript:
        """Script a streamed completion: ``tokens`` are yielded as deltas, then a
        StreamDone with the joined text. ``error`` raises before the first token.
        Returns the script so a test can later read whether it was cancelled."""
        text = "".join(tokens)
        completion = Completion(
            text=text,
            model_used=model_used,
            prompt_tokens=prompt_tokens,
            completion_tokens=max(1, len(text.split())),
            finish_reason="stop",
            trace_id=trace_id or f"fake-stream-{len(self._stream_script)}",
        )
        script = _StreamScript(tokens=list(tokens), completion=completion, error=error)
        self._stream_script.append(script)
        return script

    async def complete(
        self,
        *,
        prompt: RenderedPrompt,
        ctx: GatewayContext,
        model: ModelRef | None = None,
        response_schema: type[BaseModel] | None = None,
    ) -> Completion:
        self.calls.append(RecordedCall(prompt, ctx, model, response_schema))
        outcome = (
            self._script.pop(0)
            if self._script
            else _Outcome(self._default_completion(prompt, model), 0.0)
        )
        if outcome.delay_s:
            await asyncio.sleep(outcome.delay_s)
        if isinstance(outcome.result, Exception):
            raise outcome.result
        return outcome.result

    async def complete_stream(
        self,
        *,
        prompt: RenderedPrompt,
        ctx: GatewayContext,
        model: ModelRef | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.stream_calls.append(RecordedCall(prompt, ctx, model, None))
        script = (
            self._stream_script.pop(0)
            if self._stream_script
            else self.queue_stream(["fake ", "stream"])
        )
        if script.error is not None:
            raise script.error
        try:
            for token in script.tokens:
                yield TextDelta(token)
            yield StreamDone(script.completion)
        except GeneratorExit:
            script.cancelled = True  # the consumer cancelled mid-stream (disconnect drill)
            raise

    def _default_completion(self, prompt: RenderedPrompt, model: ModelRef | None) -> Completion:
        user_text = " ".join(m.content for m in prompt.messages if m.role == "user")
        text = f"fake completion for: {user_text[:200]}"
        self._counter += 1
        return Completion(
            text=text,
            model_used=model.name if model else "fake/model",
            prompt_tokens=sum(len(m.content.split()) for m in prompt.messages),
            completion_tokens=len(text.split()),
            finish_reason="stop",
            trace_id=f"fake-trace-{self._counter}",
        )
