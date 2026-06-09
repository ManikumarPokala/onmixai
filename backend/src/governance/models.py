"""Governance ORM models. The audit_events store lives in ``shared`` (the emitter is
cross-cutting); governance owns the retention policy that drives Task 7's purge."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.shared.database import Base


class RetentionPolicy(Base):
    """One-per-org data-retention policy. A NULL day count means 'use the platform default';
    zero/NULL is interpreted as RETAIN by the retention job (Task 7) — the safe default."""

    __tablename__ = "retention_policies"
    __table_args__ = (UniqueConstraint("org_id", name="uq_retention_policies_org_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    audit_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    conversation_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
