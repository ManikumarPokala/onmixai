"""Immutable audit store + emitter (CLAUDE.md §6, PRD Domain 9).

Every mutating operation emits one audit event through ``AuditEmitter`` — the single seam the
whole codebase already calls. Phase 6 wires durable persistence behind that seam without
touching any call site: the emitter now writes an ``audit_events`` row in the request/worker
transaction AND logs the structured line. The store is append-only — the model lives here (not
in a domain) because the emitter is cross-cutting and ``shared`` must not import a domain; the
repository has only ``add`` (no update/delete exists anywhere in the app), and the database
revokes UPDATE/DELETE from the runtime role (migration 0009). The governance domain reads it.

``event_metadata`` carries non-sensitive context only (established discipline — ids, counts,
codes; never prompt/PII content). ``request_id`` is read from the request contextvar so call
sites don't pass it.
"""

from datetime import datetime
from functools import lru_cache
from typing import Any
from uuid import UUID, uuid4

import structlog
from fastapi import Depends
from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.shared.database import Base, get_db_session

_logger = structlog.get_logger("audit")


class AuditEvent(Base):
    """An append-only audit record. Immutable: no update/delete path exists (repository has only
    ``add``; the DB revokes UPDATE/DELETE from the runtime role — migration 0009)."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_org_created", "org_id", "created_at"),
        Index("ix_audit_events_org_action_created", "org_id", "action", "created_at"),
        Index("ix_audit_events_org_resource", "org_id", "resource_type", "resource_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    actor_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(128))
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[UUID | None] = mapped_column(nullable=True)
    # DB column is "metadata"; the Python attr avoids SQLAlchemy's reserved ``metadata``.
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEventRepository:
    """Append-only writer. There is deliberately NO update or delete method — audit immutability
    is structural here, and enforced at the DB grant level (migration 0009)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, event: AuditEvent) -> None:
        """Stage an audit row in the current transaction (committed with the use case it
        records). Time: O(1)."""
        self._session.add(event)


class AuditEmitter:
    """Emits a structured audit event per mutating operation: persists an append-only row (when
    wired with a repository — always so in the app via DI/worker) and logs the structured line.
    Constructed without a repository (e.g. some unit tests) it logs only."""

    def __init__(self, repository: AuditEventRepository | None = None) -> None:
        self._repository = repository

    def emit(
        self,
        *,
        org_id: UUID,
        actor_id: UUID,
        action: str,
        resource_id: UUID | None = None,
        resource_type: str | None = None,
        **fields: Any,
    ) -> None:
        """Record one audit event. ``fields`` become non-sensitive ``metadata``; a ``trace_id``
        in ``fields`` is lifted to its column; ``request_id`` comes from the request contextvar.
        Persists in the caller's transaction (rolled back if the use case rolls back) and logs.
        Time: O(1)."""
        trace_id = fields.pop("trace_id", None)
        request_id = structlog.contextvars.get_contextvars().get("request_id")
        if self._repository is not None:
            self._repository.add(
                AuditEvent(
                    org_id=org_id,
                    actor_user_id=actor_id,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    event_metadata=fields,
                    request_id=str(request_id) if request_id is not None else None,
                    trace_id=str(trace_id) if trace_id is not None else None,
                )
            )
        _logger.info(
            "audit",
            action=action,
            org_id=str(org_id),
            actor_id=str(actor_id),
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            **fields,
        )


def get_audit_emitter(session: AsyncSession = Depends(get_db_session)) -> AuditEmitter:
    """Request-scoped emitter bound to the request session, so every ``emit`` durably records in
    the request transaction. Workers construct their own with the worker session."""
    return AuditEmitter(AuditEventRepository(session))


@lru_cache
def get_logging_audit_emitter() -> AuditEmitter:
    """A persistence-free emitter (logs only) for contexts without a session."""
    return AuditEmitter()
