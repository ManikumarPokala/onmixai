"""Ingestion worker: idempotent by construction (patterns.md §7).

The pipeline claims a document with compare-and-set (so duplicate deliveries and
two workers never both process it), parses + chunks it, writes chunks
idempotently (deterministic content hashes), and marks a user-visible terminal
state. Bounded retries with backoff; a cron sweeper recovers documents whose
worker died mid-task. Worker sessions set the tenant GUC so RLS applies exactly
as it does for requests.

Resources (session factory, storage, settings) are passed via the arq ``ctx`` so
the pipeline is testable without globals. This sprint ships a complete TXT
vertical slice; richer parsers (Task 6) and chunking strategies (Task 7) plug
into the same pipeline.
"""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.knowledge.models import Chunk
from src.knowledge.repository import ChunkRepository, DocumentRepository
from src.shared.config import Settings, get_settings
from src.shared.database import get_sessionmaker, set_tenant_context
from src.shared.queue import INGEST_TASK
from src.shared.storage import ObjectStorage, get_object_storage

_logger = structlog.get_logger("ingest")

_CHUNK_CHARS = 1000

type SessionMaker = async_sessionmaker[Any]


class RetryableError(Exception):
    """A transient ingestion failure that should be retried."""


class IngestError(Exception):
    """A permanent ingestion failure carrying a user-safe reason."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def safe_reason(exc: Exception) -> str:
    """User-visible failure reason that never leaks internals."""
    if isinstance(exc, IngestError):
        return exc.reason
    return "ingestion failed"


def _parse_txt(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IngestError("file is not valid UTF-8 text") from exc


_PARSERS = {"text/plain": _parse_txt}


def _parse(content_type: str, data: bytes) -> str:
    parser = _PARSERS.get(content_type)
    if parser is None:
        raise IngestError(f"no parser for content type {content_type}")
    return parser(data)


def _build_chunks(org_id: UUID, document_id: UUID, text: str) -> list[Chunk]:
    """Deterministic fixed-window chunking (placeholder for Task 7 strategies).

    Time: O(len(text)). Duplicate windows are de-duplicated by content hash so
    (document_id, content_hash) uniqueness holds.
    """
    chunks: list[Chunk] = []
    seen: set[str] = set()
    seq = 0
    for start in range(0, len(text), _CHUNK_CHARS):
        part = text[start : start + _CHUNK_CHARS]
        content_hash = hashlib.sha256(part.strip().encode("utf-8")).hexdigest()
        if content_hash in seen:
            continue
        seen.add(content_hash)
        chunks.append(
            Chunk(
                org_id=org_id,
                document_id=document_id,
                seq=seq,
                content=part,
                content_hash=content_hash,
                token_count=len(part.split()),
                chunk_metadata={},
            )
        )
        seq += 1
    return chunks


async def _read_object(storage: ObjectStorage, key: str) -> bytes:
    return b"".join([chunk async for chunk in storage.get_stream(key)])


async def ingest_document(ctx: dict[str, Any], document_id: str, org_id: str) -> None:
    """Claim, parse, chunk, and mark a document READY (idempotent)."""
    doc_id, oid = UUID(document_id), UUID(org_id)
    maker: SessionMaker = ctx["sessionmaker"]
    storage: ObjectStorage = ctx["storage"]
    settings: Settings = ctx["settings"]

    async with maker() as session:
        await set_tenant_context(session, oid)
        claimed = await DocumentRepository(session).claim_for_processing(
            oid, doc_id, datetime.now(UTC)
        )
        await session.commit()
    if not claimed:
        return  # duplicate delivery or already processing — loser exits silently

    async with maker() as session:
        await set_tenant_context(session, oid)
        documents = DocumentRepository(session)
        chunks = ChunkRepository(session)
        document = await documents.get(oid, doc_id)
        if document is None:
            return
        # Read attributes now; after a rollback the ORM object is expired and a
        # lazy reload would be illegal async IO.
        attempt_count = document.attempt_count
        storage_key = document.storage_key
        content_type = document.content_type
        try:
            if settings.ingest_chaos_delay_seconds > 0:
                await asyncio.sleep(settings.ingest_chaos_delay_seconds)
            data = await _read_object(storage, storage_key)
            text = _parse(content_type, data)
            await chunks.replace_for_document(oid, doc_id, _build_chunks(oid, doc_id, text))
            await documents.mark_ready(oid, doc_id)
            await session.commit()
            _logger.info("ingest.ready", document_id=document_id, org_id=org_id)
        except RetryableError:
            await session.rollback()
            await _handle_retry(ctx, maker, oid, doc_id, attempt_count, settings)
        except Exception as exc:
            await session.rollback()
            await _mark_failed(maker, oid, doc_id, safe_reason(exc))
            raise


async def _handle_retry(
    ctx: dict[str, Any],
    maker: SessionMaker,
    org_id: UUID,
    document_id: UUID,
    attempt_count: int,
    settings: Settings,
) -> None:
    exhausted = attempt_count >= settings.ingest_max_attempts
    async with maker() as session:
        await set_tenant_context(session, org_id)
        repo = DocumentRepository(session)
        if exhausted:
            await repo.mark_failed(org_id, document_id, "ingestion failed after maximum retries")
        else:
            await repo.requeue(org_id, document_id)
        await session.commit()
    if not exhausted and ctx.get("redis") is not None:
        await ctx["redis"].enqueue_job(
            INGEST_TASK,
            str(document_id),
            str(org_id),
            _defer_by=timedelta(seconds=2**attempt_count),
        )


async def _mark_failed(maker: SessionMaker, org_id: UUID, document_id: UUID, reason: str) -> None:
    async with maker() as session:
        await set_tenant_context(session, org_id)
        await DocumentRepository(session).mark_failed(org_id, document_id, reason)
        await session.commit()


async def sweep_stuck_documents(ctx: dict[str, Any]) -> None:
    """Re-queue documents stuck in PROCESSING past the deadline (dead worker)."""
    maker: SessionMaker = ctx["sessionmaker"]
    settings: Settings = ctx["settings"]
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.ingest_stuck_after_seconds)
    async with maker() as session:
        org_ids = await DocumentRepository(session).all_org_ids()

    for oid in org_ids:
        async with maker() as session:
            await set_tenant_context(session, oid)
            repo = DocumentRepository(session)
            for document in await repo.list_stuck(oid, cutoff):
                if document.attempt_count >= settings.ingest_max_attempts:
                    await repo.mark_failed(oid, document.id, "worker died repeatedly")
                    _logger.info(
                        "ingest.sweep_failed", document_id=str(document.id), org_id=str(oid)
                    )
                else:
                    await repo.requeue(oid, document.id)
                    if ctx.get("redis") is not None:
                        await ctx["redis"].enqueue_job(INGEST_TASK, str(document.id), str(oid))
                    _logger.info(
                        "ingest.sweep_requeued", document_id=str(document.id), org_id=str(oid)
                    )
            await session.commit()


async def ingest_startup(ctx: dict[str, Any]) -> None:
    """Populate the arq ctx with the worker's shared resources (on_startup hook)."""
    ctx["sessionmaker"] = get_sessionmaker()
    ctx["storage"] = get_object_storage()
    ctx["settings"] = get_settings()
