"""Report structured output — the strict contract the gateway validates the model against
(JSON mode + bounded re-ask, Phase 3). ``extra="forbid"`` rejects hallucinated fields; every
section must carry ≥1 citation marker (schema-enforced) so a report section can never be an
ungrounded assertion. API request/response schemas (Task 6) layer on top."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.reports.models import Report, ReportStatus, ReportType

# report_type → versioned prompt template (Phase 3 registry). One template per type.
TEMPLATE_FOR_TYPE: dict[ReportType, str] = {
    ReportType.EXECUTIVE_SUMMARY: "report_executive_summary",
    ReportType.TECHNICAL: "report_technical",
    ReportType.RECOMMENDATION: "report_recommendation",
}


class ReportSection(BaseModel):
    """One report section, grounded in ≥1 numbered source."""

    model_config = ConfigDict(extra="forbid")

    heading: str
    body: str
    citation_markers: list[int] = Field(min_length=1)  # ≥1 marker required (cite-or-drop)


class ReportContent(BaseModel):
    """The model's structured report (the gateway ``response_schema``)."""

    model_config = ConfigDict(extra="forbid")

    sections: list[ReportSection] = Field(min_length=1)


# --- API request/response (allow-lists; no ORM models cross the boundary, CLAUDE.md §10) ---


class CreateReportRequest(BaseModel):
    report_type: ReportType
    title: str = Field(min_length=1, max_length=255)
    query: str = Field(min_length=1, max_length=2000)
    collection_scope: list[UUID] = Field(default_factory=list)


class ReportCitationOut(BaseModel):
    """A section's citation resolved to its source (hydrated for the UI)."""

    marker_index: int
    chunk_id: UUID
    document_id: UUID
    collection_id: UUID
    filename: str
    page_ref: int | None = None


class ReportResponse(BaseModel):
    """A report at any lifecycle stage. ``sections``/``citations`` are empty until ready; a
    failed report carries a ``failure_reason`` (including the INSUFFICIENT_EVIDENCE /
    NO_GROUNDED_SECTIONS content declines) — shown honestly, never as empty success."""

    id: UUID
    report_type: ReportType
    title: str
    status: ReportStatus
    failure_reason: str | None
    sections: list[ReportSection]
    citations: list[ReportCitationOut]
    generation_metadata: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, row: Report) -> "ReportResponse":
        content: dict[str, Any] = row.content or {}
        return cls(
            id=row.id,
            report_type=row.report_type,
            title=row.title,
            status=row.status,
            failure_reason=row.failure_reason,
            sections=[ReportSection(**s) for s in content.get("sections", [])],
            citations=[ReportCitationOut(**c) for c in content.get("citations", [])],
            generation_metadata=row.generation_metadata,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class ReportPage(BaseModel):
    reports: list[ReportResponse]
    next_cursor: str | None
