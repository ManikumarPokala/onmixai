"""ARQ-backed job queue adapter (enqueue side) — the only arq SDK import site."""

from typing import Any
from uuid import UUID

from arq import create_pool
from arq.connections import RedisSettings

from src.shared.config import Settings
from src.shared.queue import EXPORT_TASK, INGEST_TASK, REPORT_TASK


class ArqJobQueue:
    """Enqueues ingestion jobs onto Redis via ARQ. Lazily opens a shared pool."""

    def __init__(self, settings: Settings) -> None:
        self._redis_url = settings.redis_url
        self._pool: Any = None

    async def _get_pool(self) -> Any:
        if self._pool is None:
            self._pool = await create_pool(RedisSettings.from_dsn(self._redis_url))
        return self._pool

    async def enqueue_ingest(self, *, document_id: UUID, org_id: UUID) -> None:
        pool = await self._get_pool()
        await pool.enqueue_job(INGEST_TASK, str(document_id), str(org_id))

    async def enqueue_report(self, *, report_id: UUID, org_id: UUID) -> None:
        pool = await self._get_pool()
        await pool.enqueue_job(REPORT_TASK, str(report_id), str(org_id))

    async def enqueue_export(self, *, export_id: UUID, org_id: UUID) -> None:
        pool = await self._get_pool()
        await pool.enqueue_job(EXPORT_TASK, str(export_id), str(org_id))

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None
