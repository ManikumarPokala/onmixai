"""AI domain ORM models: per-org model configuration, token budgets, and usage.

All four tables are tenant-owned (``org_id NOT NULL``) and protected by forced
Row-Level Security created in migration 0005 (CLAUDE.md §4). ``token_usage_events``
is append-only (no repository ever UPDATEs it); ``token_usage_periods`` holds the
materialized running total so a budget check is O(1) — never a SUM over events on
the hot path (performance.md §2). Prompt templates are deliberately NOT here — they
are versioned in-repo (ADR 0011).
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.shared.database import Base

BUDGET_PERIOD_ENUM_NAME = "budget_period"
USAGE_FEATURE_ENUM_NAME = "usage_feature"


class BudgetPeriod(StrEnum):
    """The window a token budget applies over."""

    MONTHLY = "monthly"


class UsageFeature(StrEnum):
    """The product surface that spent the tokens — for per-feature attribution."""

    CHAT = "chat"
    RECOMMENDATION = "recommendation"
    REPORT = "report"
    EVAL = "eval"
    EMBEDDING = "embedding"


def _budget_period() -> Enum:
    return Enum(
        BudgetPeriod,
        name=BUDGET_PERIOD_ENUM_NAME,
        values_callable=lambda e: [m.value for m in e],
    )


def _usage_feature() -> Enum:
    return Enum(
        UsageFeature,
        name=USAGE_FEATURE_ENUM_NAME,
        values_callable=lambda e: [m.value for m in e],
    )


class ModelConfig(Base):
    """A tenant's LLM routing config. Absent row → platform defaults from Settings."""

    __tablename__ = "model_configs"
    __table_args__ = (UniqueConstraint("org_id", name="uq_model_configs_org_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    default_model: Mapped[str] = mapped_column(String(255))
    # Ordered list of model refs tried in turn when the default fails (Task 4).
    fallback_chain: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    # NULL → inherit the platform temperature from Settings (no magic value in the DB).
    temperature_default: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TokenBudget(Base):
    """A tenant's token cap for a period (soft warn at threshold, hard block at limit)."""

    __tablename__ = "token_budgets"
    __table_args__ = (UniqueConstraint("org_id", "period", name="uq_token_budgets_org_id_period"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    period: Mapped[BudgetPeriod] = mapped_column(_budget_period())
    limit_tokens: Mapped[int] = mapped_column(BigInteger)
    soft_threshold_pct: Mapped[int] = mapped_column(Integer, server_default=text("80"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TokenUsageEvent(Base):
    """One metered LLM call. Append-only — no repository ever UPDATEs this table;
    it is the auditable source of truth, joined to traces by ``trace_id``."""

    __tablename__ = "token_usage_events"
    __table_args__ = (
        Index("ix_token_usage_events_org_id_created_at", "org_id", "created_at"),
        Index("ix_token_usage_events_org_id_feature_created_at", "org_id", "feature", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    # SET NULL (not CASCADE): usage history outlives the user who incurred it.
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    feature: Mapped[UsageFeature] = mapped_column(_usage_feature())
    model: Mapped[str] = mapped_column(String(255))
    prompt_tokens: Mapped[int] = mapped_column(Integer)
    completion_tokens: Mapped[int] = mapped_column(Integer)
    total_tokens: Mapped[int] = mapped_column(Integer)
    trace_id: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TokenUsagePeriod(Base):
    """Materialized running total for a tenant's period — the O(1) budget-check row,
    incremented transactionally with each usage event (never SUM over events)."""

    __tablename__ = "token_usage_periods"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "period_start", name="uq_token_usage_periods_org_id_period_start"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    total_tokens: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    # Set once when the period first crosses the soft threshold, so the warn log +
    # audit event fire exactly once per period (compare-and-set — Task 5).
    soft_threshold_crossed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
