"""Tracing for the gateway (CLAUDE.md §6): every LLM call emits exactly one complete
trace — on success and on each typed failure. Wired once in the ``TracingGateway``
decorator so features cannot bypass it; a provider SDK (langfuse) is confined to
``adapters/`` (import-linter). The logging exporter is dev-complete; langfuse is the
production exporter.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from time import monotonic
from typing import Protocol
from uuid import UUID

import structlog
from pydantic import BaseModel

from src.ai.gateway import (
    Completion,
    GatewayContext,
    LLMGateway,
    ModelRef,
    RenderedPrompt,
    StreamDone,
    StreamEvent,
)
from src.ai.models import UsageFeature
from src.shared.errors import AppError

_logger = structlog.get_logger("ai.trace")


@dataclass(frozen=True, slots=True)
class CompletionTrace:
    """One traced gateway call. ``trace_id`` is the join key to the usage event (success
    only); a failure carries the error class instead of completion-derived fields."""

    request_id: str
    org_id: UUID
    feature: UsageFeature
    template_name: str
    template_version: str
    model_used: str | None
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    source_chunk_ids: tuple[UUID, ...]
    finish_reason: str | None
    trace_id: str | None
    error: str | None  # exception class name on failure, else None

    def as_attributes(self) -> dict[str, object]:
        """Flatten to JSON-safe attributes for an exporter."""
        return {
            "trace_id": self.trace_id,
            "request_id": self.request_id,
            "org_id": str(self.org_id),
            "feature": self.feature.value,
            "template_name": self.template_name,
            "template_version": self.template_version,
            "model_used": self.model_used,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "latency_ms": round(self.latency_ms, 3),
            "source_chunk_ids": [str(c) for c in self.source_chunk_ids],
            "finish_reason": self.finish_reason,
            "error": self.error,
        }


class TracingPort(Protocol):
    def span(self, name: str, **attrs: object) -> AbstractContextManager[None]: ...
    def record_completion(self, trace: CompletionTrace) -> None: ...


class LoggingTracer:
    """structlog JSON exporter — dev-complete, no external account (CLAUDE.md §6)."""

    @contextmanager
    def span(self, name: str, **attrs: object) -> Iterator[None]:
        started = monotonic()
        try:
            yield
        finally:
            _logger.info(
                "ai.span", span=name, latency_ms=round((monotonic() - started) * 1000, 3), **attrs
            )

    def record_completion(self, trace: CompletionTrace) -> None:
        event = "ai.completion.error" if trace.error else "ai.completion"
        _logger.info(event, **trace.as_attributes())


class TracingGateway:
    """An ``LLMGateway`` decorator that records one trace per call — success or failure —
    then returns/re-raises unchanged."""

    def __init__(self, *, inner: LLMGateway, tracer: TracingPort) -> None:
        self._inner = inner
        self._tracer = tracer

    async def complete(
        self,
        *,
        prompt: RenderedPrompt,
        ctx: GatewayContext,
        model: ModelRef | None = None,
        response_schema: type[BaseModel] | None = None,
    ) -> Completion:
        started = monotonic()
        try:
            completion = await self._inner.complete(
                prompt=prompt, ctx=ctx, model=model, response_schema=response_schema
            )
        except AppError as exc:
            self._tracer.record_completion(
                self._trace(ctx, prompt, started, model=model, error=type(exc).__name__)
            )
            raise
        self._tracer.record_completion(self._trace(ctx, prompt, started, completion=completion))
        return completion

    async def complete_stream(
        self,
        *,
        prompt: RenderedPrompt,
        ctx: GatewayContext,
        model: ModelRef | None = None,
    ) -> AsyncIterator[StreamEvent]:
        started = monotonic()
        try:
            async for event in self._inner.complete_stream(prompt=prompt, ctx=ctx, model=model):
                if isinstance(event, StreamDone):
                    self._tracer.record_completion(
                        self._trace(ctx, prompt, started, completion=event.completion)
                    )
                yield event
        except AppError as exc:
            self._tracer.record_completion(
                self._trace(ctx, prompt, started, model=model, error=type(exc).__name__)
            )
            raise

    def _trace(
        self,
        ctx: GatewayContext,
        prompt: RenderedPrompt,
        started: float,
        *,
        completion: Completion | None = None,
        model: ModelRef | None = None,
        error: str | None = None,
    ) -> CompletionTrace:
        return CompletionTrace(
            request_id=ctx.request_id,
            org_id=ctx.org_id,
            feature=ctx.feature,
            template_name=prompt.template_name,
            template_version=prompt.template_version,
            model_used=completion.model_used if completion else (model.name if model else None),
            prompt_tokens=completion.prompt_tokens if completion else 0,
            completion_tokens=completion.completion_tokens if completion else 0,
            latency_ms=(monotonic() - started) * 1000,
            source_chunk_ids=ctx.source_chunk_ids,
            finish_reason=completion.finish_reason if completion else None,
            trace_id=completion.trace_id if completion else None,
            error=error,
        )
