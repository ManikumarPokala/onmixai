"""Recommendation service (patterns.md §1) — run the pipeline, persist the outcome, return a
DTO. A decline is a first-class persisted outcome (status=declined), not an error; an
infrastructure failure propagates from the pipeline as a typed AppError (503), nothing
persisted. Citations are stored resolved (marker → source) so a later GET hydrates them
without re-running retrieval; reads are owner-scoped (a non-owner gets 404, no oracle)."""

from typing import Any
from uuid import UUID

from src.identity.schemas import AuthContext
from src.recommendation.exceptions import RecommendationNotFoundError
from src.recommendation.models import (
    ConfidenceBand,
    Recommendation,
    RecommendationStatus,
)
from src.recommendation.pipeline import (
    CompletedRecommendation,
    Declined,
    RecommendationPipeline,
    ResolvedCitation,
)
from src.recommendation.repository import RecommendationRepository
from src.recommendation.schemas import RecommendationPage, RecommendationResponse
from src.shared.audit import AuditEmitter
from src.shared.config import Settings
from src.shared.pagination import decode_keyset_cursor, encode_keyset_cursor


class RecommendationService:
    def __init__(
        self,
        *,
        repository: RecommendationRepository,
        pipeline: RecommendationPipeline,
        audit: AuditEmitter,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._pipeline = pipeline
        self._audit = audit
        self._settings = settings

    async def create(
        self, actor: AuthContext, query: str, collection_scope: list[UUID], *, request_id: str
    ) -> RecommendationResponse:
        """Generate + persist a recommendation. Time: 1 retrieval + 1 generation. Raises
        AppError on infrastructure failure (typed error, nothing persisted)."""
        outcome = await self._pipeline.recommend(
            actor=actor, query=query, collection_scope=collection_scope, request_id=request_id
        )
        if isinstance(outcome, CompletedRecommendation):
            row = Recommendation(
                org_id=actor.org_id,
                created_by=actor.user_id,
                query=query,
                collection_scope=[str(c) for c in collection_scope],
                status=RecommendationStatus.COMPLETED,
                confidence_band=ConfidenceBand(outcome.band),
                payload={
                    "output": outcome.output.model_dump(),
                    "citations": [_citation_dict(c) for c in outcome.citations],
                },
                prompt_version=outcome.prompt_version,
                trace_id=outcome.trace_id,
            )
        else:
            assert isinstance(outcome, Declined)
            row = Recommendation(
                org_id=actor.org_id,
                created_by=actor.user_id,
                query=query,
                collection_scope=[str(c) for c in collection_scope],
                status=RecommendationStatus.DECLINED,
                decline_reason=outcome.reason,
            )
        await self._repository.add(row)
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="recommendation.created",
            resource_id=row.id,
            status=row.status.value,
            query_length=len(query),  # length + scope only — never the query content
            scope_size=len(collection_scope),
        )
        return RecommendationResponse.from_model(row)

    async def get(self, actor: AuthContext, recommendation_id: UUID) -> RecommendationResponse:
        """One recommendation the actor owns. Raises RECOMMENDATION_NOT_FOUND if it is
        absent or owned by another user (even same org) — no existence oracle. Time: O(1)."""
        row = await self._repository.get(actor.org_id, recommendation_id)
        if row is None or row.created_by != actor.user_id:
            raise RecommendationNotFoundError()
        return RecommendationResponse.from_model(row)

    async def list(
        self, actor: AuthContext, *, cursor: str | None, limit: int
    ) -> RecommendationPage:
        """One newest-first page of the actor's recommendations. Time: O(limit). Raises
        INVALID_CURSOR on a malformed cursor."""
        capped = min(limit, self._settings.rec_page_size)
        before = decode_keyset_cursor(cursor) if cursor is not None else None
        rows = await self._repository.list_for_owner(
            actor.org_id, actor.user_id, limit=capped + 1, before=before
        )
        has_more = len(rows) > capped
        page = rows[:capped]
        next_cursor = encode_keyset_cursor(page[-1].created_at, page[-1].id) if has_more else None
        return RecommendationPage(
            recommendations=[RecommendationResponse.from_model(r) for r in page],
            next_cursor=next_cursor,
        )


def _citation_dict(citation: ResolvedCitation) -> dict[str, Any]:
    page = citation.source.ref.get("page")
    return {
        "marker_index": citation.marker_index,
        "chunk_id": str(citation.chunk_id),
        "document_id": str(citation.source.document_id),
        "collection_id": str(citation.source.collection_id),
        "filename": citation.source.filename,
        "page_ref": page if isinstance(page, int) else None,
    }
