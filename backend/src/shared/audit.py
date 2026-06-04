"""Lightweight audit emitter (CLAUDE.md §6, PRD Domain 9).

Phase 1 records audit events as structured logs; Phase 6 (Governance) finalizes
the immutable append-only store that surfaces them. Emitting through this seam
now means the call sites do not change when persistence is added. Takes plain
ids (no domain imports — shared must not depend on a domain).
"""

from functools import lru_cache
from uuid import UUID

import structlog

_logger = structlog.get_logger("audit")


class AuditEmitter:
    """Emits a structured audit event per mutating operation."""

    def emit(
        self,
        *,
        org_id: UUID,
        actor_id: UUID,
        action: str,
        resource_id: UUID | None = None,
        **fields: object,
    ) -> None:
        _logger.info(
            "audit",
            action=action,
            org_id=str(org_id),
            actor_id=str(actor_id),
            resource_id=str(resource_id) if resource_id is not None else None,
            **fields,
        )


@lru_cache
def get_audit_emitter() -> AuditEmitter:
    return AuditEmitter()
