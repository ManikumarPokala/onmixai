"""Scriptable LLMGateway fake — the instrument every Phase 4–5 test uses in place of
a provider. Queue per-call completions / errors / latencies, and inspect every
recorded call (prompt + version, context, model). Same-input → same default output,
so tests are deterministic without a network."""

import asyncio
from dataclasses import dataclass

from pydantic import BaseModel

from src.ai.gateway import Completion, GatewayContext, ModelRef, RenderedPrompt


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


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[RecordedCall] = []
        self._script: list[_Outcome] = []
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
