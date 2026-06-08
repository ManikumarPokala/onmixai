"""Recommendation domain ORM model: a single, grounded, confidence-banded decision output.

The table is tenant-owned (``org_id NOT NULL``) and protected by forced Row-Level Security
created in migration 0008 (CLAUDE.md §4). A recommendation is either ``completed`` (a
schema-validated ``payload`` with a retrieval-derived ``confidence_band`` and grounded
justifications) or ``declined`` (a ``decline_reason``, no payload) — never both, never
neither (the cite-or-decline invariant, enforced by the pipeline). The band is derived from
the retrieval evidence, never from anything the model claims about its own confidence
(ADR 0016).
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.shared.database import Base

RECOMMENDATION_STATUS_ENUM_NAME = "recommendation_status"
CONFIDENCE_BAND_ENUM_NAME = "confidence_band"


class RecommendationStatus(StrEnum):
    COMPLETED = "completed"
    DECLINED = "declined"


class ConfidenceBand(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


def _recommendation_status() -> Enum:
    return Enum(
        RecommendationStatus,
        name=RECOMMENDATION_STATUS_ENUM_NAME,
        values_callable=lambda e: [m.value for m in e],
    )


def _confidence_band() -> Enum:
    return Enum(
        ConfidenceBand,
        name=CONFIDENCE_BAND_ENUM_NAME,
        values_callable=lambda e: [m.value for m in e],
    )


class Recommendation(Base):
    """One decision output for a query over a collection scope. Completed XOR declined."""

    __tablename__ = "recommendations"
    __table_args__ = (
        Index("ix_recommendations_org_creator_created", "org_id", "created_by", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    query: Mapped[str] = mapped_column(Text)
    # Collection ids the request was scoped to (empty = all permitted), as a JSON array.
    collection_scope: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[RecommendationStatus] = mapped_column(_recommendation_status())
    # Band + payload are present iff completed; decline_reason is present iff declined.
    confidence_band: Mapped[ConfidenceBand | None] = mapped_column(
        _confidence_band(), nullable=True
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    decline_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
