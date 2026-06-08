"""The grounded-answer pipeline (patterns.md §5): rewrite → retrieve → confidence →
assemble → generate → grounding-validate. It consumes only existing ports — the
permission-aware retriever (the ONLY retrieval entry) and the LLM gateway — and adds no
new provider or retrieval surface.

Cite-or-refuse is absolute for CONTENT outcomes: a low-confidence retrieval refuses
BEFORE any generation spend; a generated answer must carry valid inline [n] citation
markers (and not be majority-fabricated) or it is refused; phantom markers are stripped
and the persisted citations are the validated set only. A content outcome is a typed
AnsweredTurn or Refusal — never fabricated output. An INFRASTRUCTURE failure (gateway
outage, budget block) is NOT a content refusal: it propagates as an AppError so the SSE
layer emits an `error` event, persists no assistant row, and leaves the turn re-askable.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import structlog

from src.ai.gateway import (
    Completion,
    GatewayContext,
    LLMGateway,
    RenderedPrompt,
    StreamDone,
    TextDelta,
    UpstreamUnavailableError,
)
from src.ai.guardrails import InjectionFilter, Refusal
from src.ai.models import UsageFeature
from src.ai.prompt_registry import PromptRegistry
from src.conversation.context import HistoryTurn, assemble_context
from src.conversation.grounding import passes_confidence, validate_grounding
from src.conversation.rewrite import rewrite_query
from src.identity.schemas import AuthContext
from src.search.schemas import SearchQuery, SearchResult, SearchResultItem
from src.shared.config import Settings

_logger = structlog.get_logger("conversation.pipeline")


class Retriever(Protocol):
    """The permission-aware retrieval port (search.SearchService satisfies it)."""

    async def search(self, actor: AuthContext, query: SearchQuery) -> SearchResult: ...


@dataclass(frozen=True, slots=True)
class ResolvedCitation:
    marker_index: int
    chunk_id: UUID
    document_id: UUID
    filename: str
    page_ref: int | None


@dataclass(frozen=True, slots=True)
class AnsweredTurn:
    content: str
    citations: tuple[ResolvedCitation, ...]
    model_used: str
    prompt_version: str
    trace_id: str
    source_chunk_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class TokenChunk:
    """A streamed token delta on the way to a terminal outcome (ADR 0014)."""

    text: str


PipelineOutcome = AnsweredTurn | Refusal
# A streaming run yields zero or more TokenChunks then exactly one terminal outcome.
PipelineStreamEvent = TokenChunk | AnsweredTurn | Refusal


@dataclass(frozen=True, slots=True)
class _Prepared:
    """Everything the generation step needs, after rewrite → retrieve → confidence →
    assemble succeeded. Shared by the streaming and non-streaming paths."""

    prompt: RenderedPrompt
    ctx: GatewayContext
    kept: tuple[SearchResultItem, ...]


class GroundedAnswerPipeline:
    def __init__(
        self,
        *,
        retriever: Retriever,
        gateway: LLMGateway,
        registry: PromptRegistry,
        settings: Settings,
        injection: InjectionFilter | None = None,
    ) -> None:
        self._retriever = retriever
        self._gateway = gateway
        self._registry = registry
        self._settings = settings
        self._injection = injection or InjectionFilter()

    async def answer(
        self,
        *,
        actor: AuthContext,
        raw_query: str,
        history: list[HistoryTurn],
        summary: str | None,
        request_id: str,
    ) -> PipelineOutcome:
        """Run the pipeline (non-streaming). Time: 1 rewrite (skipped on first turn) +
        1 retrieval + 1 generation, each bounded by its own budget. Returns a content
        outcome (AnsweredTurn | Refusal); raises AppError on a gateway/infrastructure
        failure. ``answer_stream`` is the streaming twin sharing the same prep/finalize."""
        prepared = await self._prepare(
            actor=actor,
            raw_query=raw_query,
            history=history,
            summary=summary,
            request_id=request_id,
        )
        if isinstance(prepared, Refusal):
            return prepared  # low-confidence — BEFORE generation, no spend
        # A gateway AppError (provider outage, budget block) is an INFRASTRUCTURE failure,
        # not a content decision — it propagates to the caller (the SSE layer emits an
        # `error` event, persists no assistant row, and the turn is re-askable). Only
        # content outcomes (insufficient sources, ungrounded) are Refusal results.
        completion = await self._gateway.complete(prompt=prepared.prompt, ctx=prepared.ctx)
        return self._finalize(prepared, completion.text, completion)

    async def answer_stream(
        self,
        *,
        actor: AuthContext,
        raw_query: str,
        history: list[HistoryTurn],
        summary: str | None,
        request_id: str,
    ) -> AsyncIterator[PipelineStreamEvent]:
        """Stream the pipeline (ADR 0014): tokens are yielded live as ``TokenChunk``;
        when generation completes, grounding validation runs on the ASSEMBLED text and a
        single terminal ``AnsweredTurn`` or ``Refusal`` is yielded last. A low-confidence
        refusal yields no tokens at all. A gateway AppError propagates (the SSE layer emits
        an ``error`` event); validation cannot run on a prefix, so it is necessarily
        terminal. Time/Space: as ``answer`` (streaming adds no extra passes)."""
        prepared = await self._prepare(
            actor=actor,
            raw_query=raw_query,
            history=history,
            summary=summary,
            request_id=request_id,
        )
        if isinstance(prepared, Refusal):
            yield prepared  # meta → refusal — no tokens, no spend
            return
        parts: list[str] = []
        completion: Completion | None = None
        async for event in self._gateway.complete_stream(prompt=prepared.prompt, ctx=prepared.ctx):
            if isinstance(event, TextDelta):
                parts.append(event.text)
                yield TokenChunk(event.text)
            elif isinstance(event, StreamDone):
                completion = event.completion
        if completion is None:  # defensive: the stream contract guarantees a StreamDone
            raise UpstreamUnavailableError(detail="stream ended without a terminal event")
        yield self._finalize(prepared, "".join(parts), completion)

    async def _prepare(
        self,
        *,
        actor: AuthContext,
        raw_query: str,
        history: list[HistoryTurn],
        summary: str | None,
        request_id: str,
    ) -> _Prepared | Refusal:
        """rewrite → retrieve → confidence → assemble → render. Returns a ready-to-generate
        bundle, or a low-confidence Refusal that fires BEFORE any generation spend."""
        s = self._settings
        rewritten = await rewrite_query(
            history,
            raw_query,
            gateway=self._gateway,
            registry=self._registry,
            org_id=actor.org_id,
            user_id=actor.user_id,
            request_id=request_id,
            max_chars=s.chat_rewrite_max_chars,
        )

        result = await self._retriever.search(
            actor, SearchQuery(query=rewritten.query, limit=s.search_top_k)
        )
        items = result.results
        top_score = items[0].score if items else 0.0
        if not passes_confidence(
            result_count=len(items),
            top_score=top_score,
            min_results=s.chat_confidence_min_results,
            min_score=s.chat_confidence_min_score,
        ):
            return Refusal("INSUFFICIENT_SOURCES")  # BEFORE generation — no spend

        neutralized = [self._injection.neutralize(item.content) for item in items]
        assembled = assemble_context(
            history=history,
            summary=summary,
            sources=neutralized,
            budget_tokens=s.chat_context_token_budget,
            min_sources=s.chat_confidence_min_results,
        )
        kept = items[: len(assembled.sources)]  # source numbering ↔ kept items
        sources_block = "\n".join(f"[{i + 1}] {assembled.sources[i]}" for i in range(len(kept)))
        history_text = "\n".join(f"{turn.role}: {turn.content}" for turn in assembled.history)
        prompt = self._registry.render(
            "grounded_answer",
            summary=assembled.summary or "",
            history=history_text,
            sources=sources_block,
            question=raw_query,
        )
        ctx = GatewayContext(
            org_id=actor.org_id,
            user_id=actor.user_id,
            feature=UsageFeature.CHAT,
            request_id=request_id,
            source_chunk_ids=tuple(item.chunk_id for item in kept),
        )
        return _Prepared(prompt=prompt, ctx=ctx, kept=tuple(kept))

    def _finalize(self, prepared: _Prepared, text: str, completion: Completion) -> PipelineOutcome:
        """Validate grounding on the COMPLETE answer and resolve citations, or refuse.
        Shared terminal step for both the streaming and non-streaming paths."""
        kept = prepared.kept
        grounding = validate_grounding(
            text,
            num_sources=len(kept),
            max_phantom_fraction=self._settings.chat_max_phantom_fraction,
        )
        _logger.info(
            "conversation.grounding",
            trace_id=completion.trace_id,
            valid_markers=len(grounding.marker_indices),
            phantom_markers=grounding.phantom_count,  # invention rate (citation-precision eval)
        )
        if grounding.refusal_reason is not None:
            return Refusal(grounding.refusal_reason)

        citations = tuple(
            self._citation(kept[index - 1], index) for index in grounding.marker_indices
        )
        return AnsweredTurn(
            content=grounding.text,
            citations=citations,
            model_used=completion.model_used,
            prompt_version=prepared.prompt.template_version,
            trace_id=completion.trace_id,
            source_chunk_ids=tuple(item.chunk_id for item in kept),
        )

    @staticmethod
    def _citation(item: SearchResultItem, marker_index: int) -> ResolvedCitation:
        page = item.source.ref.get("page")
        return ResolvedCitation(
            marker_index=marker_index,
            chunk_id=item.chunk_id,
            document_id=item.source.document_id,
            filename=item.source.filename,
            page_ref=page if isinstance(page, int) else None,
        )
