"""Governance worker: the retention purge cron entry. The privileged purger sessionmaker is wired
into ``ctx`` by the composition root (src/worker.py); governance never constructs an engine. The
job fails loud — never silently skips — when no purger connection is configured, because audit
purging is in scope and a silent skip would leave expired data undeleted without signal."""

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.governance.purge_service import RetentionPurgeService
from src.shared.config import Settings

_logger = structlog.get_logger(__name__)


async def purge_expired_data(ctx: dict[str, Any]) -> None:
    """Enforce every org's retention policy (dry-run by config default). Time: O(orgs + deleted)."""
    settings: Settings = ctx["settings"]
    maker: async_sessionmaker[AsyncSession] | None = ctx.get("purge_sessionmaker")
    if maker is None:
        _logger.error("retention.purge_skipped", reason="no PURGE_DATABASE_URL configured")
        return
    service = RetentionPurgeService(sessionmaker=maker, settings=settings)
    reports = await service.purge(now=datetime.now(UTC), dry_run=settings.retention_dry_run)
    _logger.info(
        "retention.purge_complete",
        dry_run=settings.retention_dry_run,
        orgs=len(reports),
        audit_deleted=sum(r.audit_deleted for r in reports),
        conversation_deleted=sum(r.conversation_deleted for r in reports),
    )
