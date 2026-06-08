"""Recommendation structured output — the strict contract the gateway validates the model
against (JSON mode + bounded re-ask, Phase 3). ``extra="forbid"`` rejects hallucinated fields;
every justification must carry ≥1 citation marker (schema-enforced) so a recommendation can
never rest on an unsupported claim. Request/response API schemas (Task 4) layer on top.
"""

from pydantic import BaseModel, ConfigDict, Field


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
