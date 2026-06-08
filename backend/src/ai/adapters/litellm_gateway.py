"""litellm-backed LLMGateway adapter — the ONLY module that imports a chat provider
SDK (import-linter). It owns model resolution, bounded retry with jittered backoff,
the fallback chain, per-model circuit breaking, and structured-output validation. The
worst-case wall clock is a computed bound (asserted in tests) so the gateway never
hangs (CLAUDE.md §3.6, patterns.md §9/§10).
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from random import Random
from typing import Any, Protocol
from uuid import UUID, uuid4

import litellm
from pydantic import BaseModel, ValidationError

from src.ai.adapters.circuit_breaker import CircuitBreaker
from src.ai.gateway import (
    Completion,
    GatewayContext,
    ModelRef,
    RenderedPrompt,
    StreamDone,
    StreamEvent,
    TextDelta,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from src.ai.models import ModelConfig
from src.shared.config import Settings

# Exception classification (patterns.md §9). A provider 4xx (content policy, context
# length, auth) is the caller's/content's fault — non-retryable, surfaced immediately.
# Transport/availability failures are retryable, then fall back, then a typed 503.
_REJECTED = (
    litellm.BadRequestError,
    litellm.ContentPolicyViolationError,
    litellm.ContextWindowExceededError,
    litellm.AuthenticationError,
    litellm.PermissionDeniedError,
    litellm.NotFoundError,
)
_RETRYABLE = (
    litellm.Timeout,
    litellm.RateLimitError,
    litellm.ServiceUnavailableError,
    litellm.InternalServerError,
    litellm.APIConnectionError,
)

AcompletionFn = Callable[..., Awaitable[Any]]
SleepFn = Callable[[float], Awaitable[None]]


class ModelConfigReader(Protocol):
    async def get(self, org_id: UUID) -> ModelConfig | None: ...


class LiteLLMGateway:
    """Implements ``LLMGateway``. Dependencies are injected (config reader, breaker,
    and — for deterministic tests — the provider call, sleep, and RNG)."""

    def __init__(
        self,
        *,
        settings: Settings,
        configs: ModelConfigReader,
        breaker: CircuitBreaker,
        acompletion: AcompletionFn = litellm.acompletion,
        sleep: SleepFn = asyncio.sleep,
        rng: Random | None = None,
    ) -> None:
        self._settings = settings
        self._configs = configs
        self._breaker = breaker
        self._acompletion = acompletion
        self._sleep = sleep
        self._rng = rng or Random()

    def worst_case_wall_clock_seconds(self, chain_length: int) -> float:
        """Mathematically complete upper bound on ``complete`` wall clock — both the
        attempt timeouts AND the inter-retry backoff are counted, so 'never hangs' holds
        for any config, not just today's small backoff:

            chain × (retries+1) × timeout  +  chain × Σ_{i=0..retries-1} backoff_ceiling(i)

        where backoff_ceiling(i) = min(backoff_max, backoff_base × 2^i) is the full-jitter
        ceiling of the i-th backoff (there are ``retries`` backoffs per model). O(retries).
        """
        retries = self._settings.llm_max_retries
        attempts_ceiling = (retries + 1) * self._settings.llm_timeout_seconds
        backoff_ceiling = sum(
            min(
                self._settings.llm_backoff_max_seconds,
                self._settings.llm_backoff_base_seconds * (2**i),
            )
            for i in range(retries)
        )
        return float(chain_length * (attempts_ceiling + backoff_ceiling))

    async def complete(
        self,
        *,
        prompt: RenderedPrompt,
        ctx: GatewayContext,
        model: ModelRef | None = None,
        response_schema: type[BaseModel] | None = None,
    ) -> Completion:
        """Resolve the model chain, then try each (with retry) until one succeeds; a
        provider rejection surfaces immediately, a fully-unavailable chain → 503.

        Time: O(chain · (retries+1)) provider attempts, each bounded by the timeout.
        """
        chain, temperature = await self._resolve(ctx.org_id, model)
        trace_id = uuid4().hex
        last_detail: str | None = None
        for model_name in chain:
            if not self._breaker.allow(model_name):
                continue  # circuit OPEN → skip without attempting (no spend, no wait)
            try:
                completion = await self._attempt_model(
                    model_name, prompt, temperature, response_schema, trace_id
                )
            except UpstreamRejectedError:
                raise  # non-retryable provider rejection — immediate, no fallback
            except UpstreamUnavailableError as exc:
                self._breaker.record_failure(model_name)
                last_detail = exc.detail
                continue  # advance the fallback chain
            self._breaker.record_success(model_name)
            return completion
        raise UpstreamUnavailableError(
            detail=last_detail or "all providers unavailable or circuit-open"
        )

    async def complete_stream(
        self,
        *,
        prompt: RenderedPrompt,
        ctx: GatewayContext,
        model: ModelRef | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream the first circuit-allowed model's completion. Streaming does NOT retry
        or fall back mid-stream (an error after the first token propagates); the breaker
        still gates the initial attempt and records the outcome. Non-streaming complete()
        keeps the full retry/fallback chain. Token counts are estimated (the dev stub does
        not emit streamed usage)."""
        chain, temperature = await self._resolve(ctx.org_id, model)
        trace_id = uuid4().hex
        model_name = next((name for name in chain if self._breaker.allow(name)), None)
        if model_name is None:
            raise UpstreamUnavailableError(detail="all providers circuit-open")

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": [{"role": m.role, "content": m.content} for m in prompt.messages],
            "temperature": temperature,
            "timeout": self._settings.llm_timeout_seconds,
            "num_retries": 0,
            "stream": True,
        }
        if self._settings.llm_base_url:
            kwargs["api_base"] = self._settings.llm_base_url
        if self._settings.llm_api_key:
            kwargs["api_key"] = self._settings.llm_api_key.get_secret_value()

        parts: list[str] = []
        try:
            response = await self._acompletion(**kwargs)
            async for chunk in response:
                delta = chunk.choices[0].delta.content
                if delta:
                    parts.append(delta)
                    yield TextDelta(delta)
        except _REJECTED as exc:
            self._breaker.record_failure(model_name)
            raise UpstreamRejectedError(detail=str(exc)) from exc
        except (*_RETRYABLE, litellm.APIError) as exc:
            self._breaker.record_failure(model_name)
            raise UpstreamUnavailableError(detail=str(exc)) from exc

        self._breaker.record_success(model_name)
        text = "".join(parts)
        yield StreamDone(
            Completion(
                text=text,
                model_used=model_name,
                prompt_tokens=sum(len(m.content.split()) for m in prompt.messages),
                completion_tokens=max(1, len(text.split())),
                finish_reason="stop",
                trace_id=trace_id,
            )
        )

    async def _resolve(self, org_id: UUID, model: ModelRef | None) -> tuple[list[str], float]:
        config = await self._configs.get(org_id)
        if model is not None:
            chain = [model.name]
        elif config is not None:
            chain = [config.default_model, *config.fallback_chain]
        else:
            chain = [self._settings.llm_default_model, *self._settings.llm_fallback_chain]
        temperature = (
            config.temperature_default
            if config is not None and config.temperature_default is not None
            else self._settings.llm_temperature_default
        )
        # De-dupe, order-preserving: a model listed twice must not be tried twice.
        seen: set[str] = set()
        ordered: list[str] = []
        for name in chain:
            if name not in seen:
                seen.add(name)
                ordered.append(name)
        return ordered, temperature

    async def _attempt_model(
        self,
        model_name: str,
        prompt: RenderedPrompt,
        temperature: float,
        response_schema: type[BaseModel] | None,
        trace_id: str,
    ) -> Completion:
        retries = self._settings.llm_max_retries
        for attempt in range(retries + 1):
            try:
                return await self._call(model_name, prompt, temperature, response_schema, trace_id)
            except _REJECTED as exc:
                raise UpstreamRejectedError(detail=str(exc)) from exc
            except _RETRYABLE as exc:
                if attempt >= retries:
                    raise UpstreamUnavailableError(detail=str(exc)) from exc
                await self._sleep(self._backoff(attempt))
            except litellm.APIError as exc:
                status = getattr(exc, "status_code", None)
                # 4xx is the caller's fault → reject, EXCEPT 408 (request timeout) and
                # 429 (rate limit), which are transient and retryable like 5xx.
                if status is not None and 400 <= status < 500 and status not in (408, 429):
                    raise UpstreamRejectedError(detail=str(exc)) from exc
                if attempt >= retries:
                    raise UpstreamUnavailableError(detail=str(exc)) from exc
                await self._sleep(self._backoff(attempt))
        raise UpstreamUnavailableError(detail="retries exhausted")  # pragma: no cover

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with full jitter, capped. Time/Space: O(1)."""
        capped = min(
            self._settings.llm_backoff_max_seconds,
            self._settings.llm_backoff_base_seconds * (2**attempt),
        )
        return self._rng.uniform(0, capped)

    async def _call(
        self,
        model_name: str,
        prompt: RenderedPrompt,
        temperature: float,
        response_schema: type[BaseModel] | None,
        trace_id: str,
    ) -> Completion:
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": [{"role": m.role, "content": m.content} for m in prompt.messages],
            "temperature": temperature,
            "timeout": self._settings.llm_timeout_seconds,
            "num_retries": 0,  # our retry policy is the only one
        }
        if self._settings.llm_base_url:
            kwargs["api_base"] = self._settings.llm_base_url
        if self._settings.llm_api_key:
            kwargs["api_key"] = self._settings.llm_api_key.get_secret_value()
        if response_schema is not None:
            kwargs["response_format"] = {"type": "json_object"}

        completion = self._to_completion(await self._acompletion(**kwargs), model_name, trace_id)
        if response_schema is not None and not self._valid(completion.text, response_schema):
            # One bounded re-ask, then a typed rejection (never fabricated output).
            completion = self._to_completion(
                await self._acompletion(**kwargs), model_name, trace_id
            )
            if not self._valid(completion.text, response_schema):
                raise UpstreamRejectedError(
                    "SCHEMA_VALIDATION_FAILED",
                    detail="response failed schema validation after re-ask",
                )
        return completion

    @staticmethod
    def _to_completion(response: Any, model_name: str, trace_id: str) -> Completion:
        choice = response.choices[0]
        usage = response.usage
        return Completion(
            text=choice.message.content or "",
            model_used=model_name,  # the resolved chain entry — deterministic attribution
            prompt_tokens=getattr(usage, "prompt_tokens", 0),
            completion_tokens=getattr(usage, "completion_tokens", 0),
            finish_reason=choice.finish_reason or "stop",
            trace_id=trace_id,
        )

    @staticmethod
    def _valid(text: str, schema: type[BaseModel]) -> bool:
        try:
            schema.model_validate_json(text)
        except (ValidationError, ValueError):
            return False
        return True
