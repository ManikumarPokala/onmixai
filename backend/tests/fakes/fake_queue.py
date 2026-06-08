"""In-memory JobQueue fake — records enqueued ingestion jobs."""

from uuid import UUID


class FakeJobQueue:
    """Implements the JobQueue Protocol; records (document_id, org_id) tuples."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[UUID, UUID]] = []
        self.reports: list[tuple[UUID, UUID]] = []
        self.exports: list[tuple[UUID, UUID]] = []
        self.closed: bool = False

    async def enqueue_ingest(self, *, document_id: UUID, org_id: UUID) -> None:
        self.enqueued.append((document_id, org_id))

    async def enqueue_report(self, *, report_id: UUID, org_id: UUID) -> None:
        self.reports.append((report_id, org_id))

    async def enqueue_export(self, *, export_id: UUID, org_id: UUID) -> None:
        self.exports.append((export_id, org_id))

    async def close(self) -> None:
        self.closed = True
