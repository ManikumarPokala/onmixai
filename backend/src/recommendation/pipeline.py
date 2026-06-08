"""The recommendation pipeline (patterns.md §5): retrieve → confidence band → decline gate →
assemble grounded context → generate structured output → ground-validate justifications →
attach the retrieval-derived band. It consumes only existing ports — the permission-aware
retriever (the ONLY retrieval entry) and the LLM gateway — and adds no new surface.

Single-model, grounded, decline-or-cite:
- A below-floor / empty retrieval declines BEFORE any generation spend (zero cost).
- The model output is schema-validated by the gateway (JSON mode + bounded re-ask, Phase 3);
  a persistently invalid output is a typed gateway error, NOT a silent default.
- Every justification's citations are validated; an emptied justification declines the whole
  recommendation (a recommendation cannot rest on an unsupported claim).
- The confidence band is attached FROM THE RETRIEVAL SCORES, never from anything the model
  claims about its own confidence (ADR 0016) — the schema has no confidence field at all.
- An infrastructure failure (gateway outage, budget block) is a typed AppError, NOT a decline
  — declines and errors are never conflated (the Phase-4 distinction, reused).
"""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import structlog

from src.ai.gateway import GatewayContext, LLMGateway
from src.ai.guardrails import InjectionFilter
from src.ai.models import UsageFeature
from src.ai.prompt_registry import PromptRegistry
from src.identity.schemas import AuthContext
from src.recommendation.rules import (
    DeclineReason,
    confidence_band_from_scores,
    ensure_justifications_grounded,
    should_decline,
)
from src.recommendation.schemas import RecommendationOutput
from src.search.schemas import SearchQuery, SearchResult, SearchResultItem, SourceAttribution
from src.shared.config import Settings

_logger = structlog.get_logger("recommendation.pipeline")


class Retriever(Protocol):
    """The permission-aware retrieval port (search.SearchService satisfies it)."""

    async def search(self, actor: AuthContext, query: SearchQuery) -> SearchResult: ...


@dataclass(frozen=True, slots=True)
class ResolvedCitation:
    marker_index: int
    chunk_id: UUID
    source: SourceAttribution


@dataclass(frozen=True, slots=True)
class CompletedRecommendation:
    output: RecommendationOutput  # grounded, phantom-stripped structured output
    band: str  # confidence band (high|medium|low) — DERIVED FROM RETRIEVAL, not the model
    citations: tuple[ResolvedCitation, ...]
    prompt_version: str
    trace_id: str
    source_chunk_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class Declined:
    reason: DeclineReason


RecommendationOutcome = CompletedRecommendation | Declined


class RecommendationPipeline:
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

    async def recommend(
        self,
        *,
        actor: AuthContext,
        query: str,
        collection_scope: list[UUID],
        request_id: str,
    ) -> RecommendationOutcome:
        """Run the pipeline. Time: 1 retrieval (per scoped collection) + 1 generation. Returns
        a content outcome (CompletedRecommendation | Declined); raises AppError on a
        gateway/infrastructure failure (typed error, not a decline)."""
        s = self._settings
        items = await self._retrieve(actor, query, collection_scope)
        scores = [item.score for item in items]
        band = confidence_band_from_scores(
            scores,
            top_k=s.rec_confidence_top_k,
            high=s.rec_confidence_high,
            medium=s.rec_confidence_medium,
            floor=s.rec_confidence_floor,
        )
        decline = should_decline(scores, band)
        if decline is not None:
            return Declined(decline)  # BEFORE generation — no spend
        assert band is not None

        kept = items[: s.rec_retrieval_top_k]
        sources_block = "\n".join(
            f"[{i + 1}] {self._injection.neutralize(kept[i].content)}" for i in range(len(kept))
        )
        prompt = self._registry.render("recommendation", sources=sources_block, query=query)
        ctx = GatewayContext(
            org_id=actor.org_id,
            user_id=actor.user_id,
            feature=UsageFeature.RECOMMENDATION,
            request_id=request_id,
            source_chunk_ids=tuple(item.chunk_id for item in kept),
        )
        # A persistently schema-invalid output or an infrastructure failure propagates as a
        # typed AppError — never a fabricated default, never silently downgraded to a decline.
        completion = await self._gateway.complete(
            prompt=prompt, ctx=ctx, response_schema=RecommendationOutput
        )
        output = RecommendationOutput.model_validate_json(completion.text)

        valid_markers = frozenset(range(1, len(kept) + 1))
        grounding = ensure_justifications_grounded(output, valid_markers)
        _logger.info(
            "recommendation.grounding",
            trace_id=completion.trace_id,
            band=band,  # attached from retrieval scores, NOT the model's self-report
            phantom_markers=grounding.phantom_count,
        )
        if grounding.output is None:
            return Declined("INSUFFICIENT_EVIDENCE")  # a claim lost all support → cannot stand

        citations = self._citations(grounding.output, kept)
        return CompletedRecommendation(
            output=grounding.output,
            band=band,
            citations=citations,
            prompt_version=prompt.template_version,
            trace_id=completion.trace_id,
            source_chunk_ids=tuple(item.chunk_id for item in kept),
        )

    async def _retrieve(
        self, actor: AuthContext, query: str, collection_scope: list[UUID]
    ) -> list[SearchResultItem]:
        """Retrieve the candidate evidence. With no scope, one permission-filtered retrieval;
        with a scope, one retrieval per collection merged by score (dedup chunk_id), top-k.
        Time: O(scope · top_k). Space: O(top_k)."""
        top_k = self._settings.rec_retrieval_top_k
        if not collection_scope:
            result = await self._retriever.search(actor, SearchQuery(query=query, limit=top_k))
            return result.results
        merged: dict[UUID, SearchResultItem] = {}
        for collection_id in collection_scope:
            result = await self._retriever.search(
                actor, SearchQuery(query=query, collection_id=collection_id, limit=top_k)
            )
            for item in result.results:
                if item.chunk_id not in merged or item.score > merged[item.chunk_id].score:
                    merged[item.chunk_id] = item
        return sorted(merged.values(), key=lambda item: -item.score)[:top_k]

    def _citations(
        self, output: RecommendationOutput, kept: list[SearchResultItem]
    ) -> tuple[ResolvedCitation, ...]:
        markers = sorted({m for j in output.justifications for m in j.citation_markers})
        return tuple(
            ResolvedCitation(
                marker_index=marker,
                chunk_id=kept[marker - 1].chunk_id,
                source=kept[marker - 1].source,
            )
            for marker in markers
        )
