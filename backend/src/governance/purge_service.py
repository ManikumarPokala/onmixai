"""Retention purge — the destructive enforcement of each org's retention policy (ADR 0019).

Design invariants (Task 7):
  * Retain-by-default. A class with no policy, or a null/zero window, yields no cutoff (governance
    rules.retention_cutoff) → that class is never touched. The safe default is zero deletions.
  * Audit before delete, atomically. Each batch is one transaction that INSERTs the purge audit
    record (flushed first) and then DELETEs the rows it named. A deletion can never be unaudited —
    a crash before commit rolls back both; a crash after commit persists both.
  * Crash-resumable by construction. Each bounded batch commits independently and deletion advances
    the frontier of expired rows, so a re-run after a crash simply continues — every row is deleted
    in exactly one committed batch (patterns.md §7), no cursor to persist.
  * Privileged role only. Runs on the dedicated purger connection (PURGE_DATABASE_URL); the runtime
    role's audit DELETE stays REVOKEd (migration 0009). RLS still scopes every statement per org —
    tenant context is set per batch (it is transaction-scoped, so it is re-set after each commit).

Cross-org enumeration reads ``organizations`` (the tenant root, not RLS-scoped); per-org reads and
deletes run under that org's tenant context.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.governance.repository import RetentionPolicyRepository, RetentionPurgeRepository
from src.governance.rules import retention_cutoff
from src.governance.schemas import PurgeReport
from src.shared.audit import AuditEvent
from src.shared.config import Settings
from src.shared.database import set_tenant_context

_AUDIT = "audit_events"
_CONVERSATION = "chat_sessions"


class RetentionPurgeService:
    """Enforces retention policies by deleting expired data on a privileged session. Owns its own
    transactions (commit per batch); inject the privileged sessionmaker so it is unit-testable."""

    def __init__(
        self, *, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings

    async def purge(self, *, now: datetime, dry_run: bool) -> list[PurgeReport]:
        """Purge expired data for every org with a retention policy. ``now`` must be tz-aware.
        Time: O(orgs + deleted rows). Space: O(batch)."""
        async with self._sessionmaker() as session:
            org_ids = await RetentionPurgeRepository(session).all_org_ids()
        reports: list[PurgeReport] = []
        for org_id in org_ids:
            report = await self._purge_org(org_id, now=now, dry_run=dry_run)
            if report is not None:
                reports.append(report)
        return reports

    async def _purge_org(self, org_id: UUID, *, now: datetime, dry_run: bool) -> PurgeReport | None:
        async with self._sessionmaker() as session:
            await set_tenant_context(session, org_id)
            policy = await RetentionPolicyRepository(session).get(org_id)
        if policy is None:
            return None  # retain-by-default: no policy → nothing to purge
        audit_cutoff = retention_cutoff(policy.audit_retention_days, now=now)
        conversation_cutoff = retention_cutoff(policy.conversation_retention_days, now=now)
        audit_deleted = await self._purge_class(
            org_id, _AUDIT, audit_cutoff, "retention.audit_purged", dry_run=dry_run
        )
        conversation_deleted = await self._purge_class(
            org_id,
            _CONVERSATION,
            conversation_cutoff,
            "retention.conversation_purged",
            dry_run=dry_run,
        )
        return PurgeReport(
            org_id=org_id,
            audit_deleted=audit_deleted,
            conversation_deleted=conversation_deleted,
            dry_run=dry_run,
        )

    async def _purge_class(
        self, org_id: UUID, table: str, cutoff: datetime | None, action: str, *, dry_run: bool
    ) -> int:
        if cutoff is None:
            return 0  # retain-by-default for this class
        if dry_run:
            return await self._dry_run_class(org_id, table, cutoff, action)
        total = 0
        while True:
            async with self._sessionmaker() as session:
                await set_tenant_context(session, org_id)
                repo = RetentionPurgeRepository(session)
                ids = await repo.expired_id_batch(
                    table, org_id, cutoff, self._settings.retention_batch_size
                )
                if not ids:
                    break
                # Audit BEFORE delete, same transaction: flush the record first, then delete the
                # rows it names — so no deletion is ever unaudited, and both are atomic.
                session.add(_purge_event(org_id, action, table, cutoff, deleted=len(ids)))
                await session.flush()
                total += await repo.delete_ids(table, org_id, ids)
                await session.commit()
        return total

    async def _dry_run_class(self, org_id: UUID, table: str, cutoff: datetime, action: str) -> int:
        async with self._sessionmaker() as session:
            await set_tenant_context(session, org_id)
            count = await RetentionPurgeRepository(session).count_expired(table, org_id, cutoff)
            session.add(_purge_event(org_id, action, table, cutoff, candidates=count, dry_run=True))
            await session.commit()
        return count


def _purge_event(
    org_id: UUID,
    action: str,
    table: str,
    cutoff: datetime,
    *,
    deleted: int | None = None,
    candidates: int | None = None,
    dry_run: bool = False,
) -> AuditEvent:
    """A system-initiated (null-actor) audit record naming exactly what the batch purged. JSON-safe
    metadata (cutoff as ISO-8601)."""
    metadata: dict[str, object] = {"table": table, "cutoff": cutoff.isoformat(), "dry_run": dry_run}
    if deleted is not None:
        metadata["deleted"] = deleted
    if candidates is not None:
        metadata["candidates"] = candidates
    return AuditEvent(
        org_id=org_id,
        actor_user_id=None,
        action=action,
        resource_type="retention",
        event_metadata=metadata,
    )
