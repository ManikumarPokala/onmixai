"""Recommendation structured output — the strict contract the gateway validates the model
against (JSON mode + bounded re-ask, Phase 3). ``extra="forbid"`` rejects hallucinated fields;
every justification must carry ≥1 citation marker (schema-enforced) so a recommendation can
never rest on an unsupported claim. Request/response API schemas (Task 4) layer on top.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.recommendation.models import ConfidenceBand, Recommendation, RecommendationStatus


class Alternative(BaseModel):
    """A considered-but-not-chosen option and why."""

    model_config = ConfigDict(extra="forbid")

    option: str
    rationale: str


class Justification(BaseModel):
    """A claim supporting the recommendation, grounded in ≥1 numbered source."""

    model_config = ConfigDict(extra="forbid")

    claim: str
    citation_markers: list[int] = Field(min_length=1)  # ≥1 marker required (cite-or-decline)


class RecommendationOutput(BaseModel):
    """The model's structured decision output (the gateway ``response_schema``)."""

    model_config = ConfigDict(extra="forbid")

    recommendation: str
    alternatives: list[Alternative]
    justifications: list[Justification]
    caveats: list[str]


# --- API request/response (allow-lists; no ORM models cross the boundary, CLAUDE.md §10) ---


class CreateRecommendationRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    collection_scope: list[UUID] = Field(default_factory=list)


class CitationOut(BaseModel):
    """A justification's citation resolved to its source (hydrated for the UI)."""

    marker_index: int
    chunk_id: UUID
    document_id: UUID
    collection_id: UUID
    filename: str
    page_ref: int | None = None


class RecommendationResponse(BaseModel):
    """A recommendation outcome — completed (band + grounded output + citations) or declined
    (a reason). A decline is a valid 200 outcome, distinguished by ``status``."""

    id: UUID
    status: RecommendationStatus
    confidence_band: ConfidenceBand | None
    recommendation: str | None
    alternatives: list[Alternative]
    justifications: list[Justification]
    caveats: list[str]
    citations: list[CitationOut]
    decline_reason: str | None
    prompt_version: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, row: Recommendation) -> "RecommendationResponse":
        payload: dict[str, Any] = row.payload or {}
        output: dict[str, Any] = payload.get("output", {})
        citations: list[dict[str, Any]] = payload.get("citations", [])
        return cls(
            id=row.id,
            status=row.status,
            confidence_band=row.confidence_band,
            recommendation=output.get("recommendation"),
            alternatives=[Alternative(**a) for a in output.get("alternatives", [])],
            justifications=[Justification(**j) for j in output.get("justifications", [])],
            caveats=output.get("caveats", []),
            citations=[CitationOut(**c) for c in citations],
            decline_reason=row.decline_reason,
            prompt_version=row.prompt_version,
            created_at=row.created_at,
        )


class RecommendationPage(BaseModel):
    recommendations: list[RecommendationResponse]
    next_cursor: str | None
