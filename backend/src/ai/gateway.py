"""The LLM gateway contract — the single doorway every feature imports (CLAUDE.md §3.6).

No feature talks to a provider directly; they depend on the ``LLMGateway`` Protocol
and these typed, immutable value objects. The concrete adapter (litellm) lives in
``ai/adapters/`` — the only place a provider SDK may be imported (import-linter). The
error taxonomy follows patterns.md §9: a typed result for every outcome, never a hang
and never fabricated output on failure.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel

from src.ai.models import UsageFeature
from src.shared.errors import AppError


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One message in a prompt. ``role`` is the OpenAI role (system/user/assistant)."""

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    """A prompt template rendered to concrete messages. ``template_version`` and
    ``variables_hash`` flow into the trace + usage event so a completion is always
    attributable to an exact, hash-pinned template version (Task 7)."""

    template_name: str
    template_version: str
    messages: tuple[ChatMessage, ...]
    variables_hash: str


@dataclass(frozen=True, slots=True)
class ModelRef:
    """A litellm-style model reference, e.g. ``"openai/gpt-4o-mini"``."""

    name: str


@dataclass(frozen=True, slots=True)
class GatewayContext:
    """Who/what a completion is for — carried into metering and tracing. The
    ``source_chunk_ids`` are the retrieved chunks grounding this call (traced for
    auditability), immutable so a step can't mutate another step's provenance."""

    org_id: UUID
    user_id: UUID
    feature: UsageFeature
    request_id: str
    source_chunk_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class Completion:
    """A successful model response with the token counts that get metered + traced."""

    text: str
    model_used: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str
    trace_id: str

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class UpstreamUnavailableError(AppError):
    """Retries + fallbacks exhausted (every provider down). Maps to 503 — the request
    never hangs and never returns fabricated output."""

    def __init__(
        self, *, message: str = "AI provider temporarily unavailable", detail: str | None = None
    ) -> None:
        super().__init__(code="UPSTREAM_UNAVAILABLE", status=503, message=message, detail=detail)


class UpstreamRejectedError(AppError):
    """A provider rejected the request (content policy, context length) — a non-retryable
    4xx. The client gets a safe message; the full provider detail is logged, never returned."""

    def __init__(
        self,
        code: str = "UPSTREAM_REJECTED",
        *,
        message: str = "The request was rejected by the AI provider",
        detail: str | None = None,
    ) -> None:
        super().__init__(code=code, status=422, message=message, detail=detail)


class BudgetExceededError(AppError):
    """The org's token budget is exhausted — blocked BEFORE any provider call (no spend
    on a blocked request). A typed 429 (quota), distinct from a transport rate limit."""

    def __init__(
        self, *, message: str = "Token budget exceeded for this period", detail: str | None = None
    ) -> None:
        super().__init__(code="BUDGET_EXCEEDED", status=429, message=message, detail=detail)


class GuardrailViolationError(AppError):
    """A guardrail blocked the request or response (injection, failed grounding/schema)."""

    def __init__(
        self,
        code: str = "GUARDRAIL_VIOLATION",
        *,
        message: str = "The request violated a safety guardrail",
        detail: str | None = None,
    ) -> None:
        super().__init__(code=code, status=422, message=message, detail=detail)


@dataclass(frozen=True, slots=True)
class TextDelta:
    """A streamed token delta."""

    text: str


@dataclass(frozen=True, slots=True)
class StreamDone:
    """Terminal stream event carrying the assembled completion (text + token counts +
    trace_id) — the same metering/tracing data a non-streamed completion produces."""

    completion: Completion


StreamEvent = TextDelta | StreamDone


class LLMGateway(Protocol):
    """The one interface to every LLM. Implementations own routing, bounded retry,
    fallback, circuit-breaking, metering, budget enforcement, tracing, and structured-
    output validation — features compose this, never a provider SDK."""

    async def complete(
        self,
        *,
        prompt: RenderedPrompt,
        ctx: GatewayContext,
        model: ModelRef | None = None,
        response_schema: type[BaseModel] | None = None,
    ) -> Completion: ...

    def complete_stream(
        self,
        *,
        prompt: RenderedPrompt,
        ctx: GatewayContext,
        model: ModelRef | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a completion as ``TextDelta`` events, ending with one ``StreamDone``.
        Metering + tracing happen on completion (the StreamDone), exactly as for
        ``complete`` — grounding validation runs on the assembled text (ADR 0014)."""
        ...
