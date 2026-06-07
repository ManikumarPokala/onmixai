"""The grounded-answer pipeline (patterns.md §5): rewrite → retrieve → confidence →
assemble → generate → grounding-validate. It consumes only existing ports — the
permission-aware retriever (the ONLY retrieval entry) and the LLM gateway — and adds no
new provider or retrieval surface.

Cite-or-refuse is absolute: a low-confidence retrieval refuses BEFORE any generation
spend; a generated answer must carry valid inline [n] citation markers or it is refused;
phantom markers are stripped and the persisted citations are the validated set only.
Every outcome is a typed AnsweredTurn or Refusal — never fabricated output.
"""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from src.ai.gateway import GatewayContext, LLMGateway
from src.ai.guardrails import InjectionFilter, Refusal
from src.ai.models import UsageFeature
from src.ai.prompt_registry import PromptRegistry
from src.conversation.context import HistoryTurn, assemble_context
from src.conversation.grounding import passes_confidence, validate_grounding
from src.conversation.rewrite import rewrite_query
from src.identity.schemas import AuthContext
from src.search.schemas import SearchQuery, SearchResult, SearchResultItem
from src.shared.config import Settings
from src.shared.errors import AppError


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


PipelineOutcome = AnsweredTurn | Refusal


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
        """Run the pipeline. Time: 1 rewrite (skipped on first turn) + 1 retrieval +
        1 generation, each bounded by its own budget. Returns a typed outcome."""
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
        try:
            completion = await self._gateway.complete(prompt=prompt, ctx=ctx)
        except AppError:
            return Refusal("GENERATION_FAILED")  # typed — never a fabricated answer

        grounding = validate_grounding(completion.text, num_sources=len(kept))
        if grounding.refusal_reason is not None:
            return Refusal(grounding.refusal_reason)

        citations = tuple(
            self._citation(kept[index - 1], index) for index in grounding.marker_indices
        )
        return AnsweredTurn(
            content=grounding.text,
            citations=citations,
            model_used=completion.model_used,
            prompt_version=prompt.template_version,
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
