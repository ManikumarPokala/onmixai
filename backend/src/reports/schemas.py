"""Report structured output — the strict contract the gateway validates the model against
(JSON mode + bounded re-ask, Phase 3). ``extra="forbid"`` rejects hallucinated fields; every
section must carry ≥1 citation marker (schema-enforced) so a report section can never be an
ungrounded assertion. API request/response schemas (Task 6) layer on top."""

from pydantic import BaseModel, ConfigDict, Field

from src.reports.models import ReportType

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
