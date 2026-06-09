"""Governance API schemas (allow-lists; org_id is implicit — the actor's org — never echoed)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from src.governance.models import RetentionPolicy
from src.shared.audit import AuditEvent


class AuditFilter(BaseModel):
    """Internal filter DTO for an audit query (all optional; combine as an AND predicate)."""

    actor_user_id: UUID | None = None
    action: str | None = None
    resource_type: str | None = None
    resource_id: UUID | None = None
    start: datetime | None = None  # inclusive lower bound on created_at
    end: datetime | None = None  # exclusive upper bound on created_at


class AuditEventResponse(BaseModel):
    """One audit row for the admin viewer. ``metadata`` is non-sensitive by construction."""

    id: UUID
    actor_user_id: UUID
    action: str
    resource_type: str | None
    resource_id: UUID | None
    metadata: dict[str, Any]
    request_id: str | None
    trace_id: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, row: AuditEvent) -> "AuditEventResponse":
        return cls(
            id=row.id,
            actor_user_id=row.actor_user_id,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            metadata=row.event_metadata,
            request_id=row.request_id,
            trace_id=row.trace_id,
            created_at=row.created_at,
        )


class AuditEventPage(BaseModel):
    events: list[AuditEventResponse]
    next_cursor: str | None


class UsageAnalytics(BaseModel):
    """Org-scoped usage over a window [start, end). Token figures are windowed; document/storage
    figures are current (live, non-superseded)."""

    start: datetime
    end: datetime
    tokens_total: int
    tokens_by_feature: dict[str, int]
    document_count: int
    storage_bytes: int
    search_count: int
    active_users: int


class RetentionPolicyResponse(BaseModel):
    """The org's retention policy. Null day counts mean retain-by-default (the safe default the
    Task-7 purge job honours — null/zero/missing → zero deletions)."""

    audit_retention_days: int | None
    conversation_retention_days: int | None

    @classmethod
    def from_model(cls, policy: "RetentionPolicy") -> "RetentionPolicyResponse":
        return cls(
            audit_retention_days=policy.audit_retention_days,
            conversation_retention_days=policy.conversation_retention_days,
        )

    @classmethod
    def retain_by_default(cls) -> "RetentionPolicyResponse":
        return cls(audit_retention_days=None, conversation_retention_days=None)


class SetRetentionPolicyRequest(BaseModel):
    """Set the org's retention windows. None (or omitted) means retain forever for that data
    class; a positive day count enables time-based purging (enforced by the Task-7 job)."""

    audit_retention_days: int | None = Field(default=None, ge=1)
    conversation_retention_days: int | None = Field(default=None, ge=1)
