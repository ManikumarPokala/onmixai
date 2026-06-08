"""Ingestion job queue Protocol owned by us (patterns.md §6).

The enqueue side ships here; the ARQ worker that consumes ``INGEST_TASK`` is
wired in Task 5. Business code depends on this Protocol; the arq SDK is imported
only in ``adapters/arq_queue.py``.
"""

from functools import lru_cache
from typing import Protocol
from uuid import UUID

from src.shared.config import get_settings

# Job names shared between the enqueue side and the worker functions.
INGEST_TASK = "ingest_document"
REPORT_TASK = "generate_report"
EXPORT_TASK = "export_report_pdf"


class JobQueue(Protocol):
    async def enqueue_ingest(self, *, document_id: UUID, org_id: UUID) -> None:
        """Enqueue an ingestion job for a committed document."""
        ...

    async def enqueue_report(self, *, report_id: UUID, org_id: UUID) -> None:
        """Enqueue a report-generation job for a committed (queued) report."""
        ...

    async def enqueue_export(self, *, export_id: UUID, org_id: UUID) -> None:
        """Enqueue a PDF-export job for a committed (queued) export."""
        ...

    async def close(self) -> None:
        """Release the queue connection (called on application shutdown)."""
        ...


@lru_cache
def get_job_queue() -> JobQueue:
    """Process-wide job queue, constructed from settings on first use."""
    from src.shared.adapters.arq_queue import ArqJobQueue

    return ArqJobQueue(get_settings())
