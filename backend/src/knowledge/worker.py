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
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.ai.embedding import Embedder, Vector
from src.knowledge.chunking import ChunkParams, ChunkPiece, dedupe_by_hash
from src.knowledge.ingest_errors import RetryableError, safe_reason
from src.knowledge.parsing.base import ParsedDocument
from src.knowledge.parsing.registry import ParserRegistry
from src.knowledge.repository import (
    ChunkRepository,
    DocumentRepository,
    StorageOutboxRepository,
)
from src.knowledge.rules import select_chunking_strategy
from src.shared.config import Settings, get_settings
from src.shared.database import get_sessionmaker, set_tenant_context
from src.shared.queue import INGEST_TASK
from src.shared.storage import ObjectStorage, get_object_storage

_logger = structlog.get_logger("ingest")

type SessionMaker = async_sessionmaker[Any]

__all__ = [
    "RetryableError",
    "ingest_document",
    "ingest_startup",
    "sweep_storage_outbox",
    "sweep_stuck_documents",
]


class TenantLister(Protocol):
    """Tenant enumeration the sweeper needs; identity's OrgPolicyService satisfies it."""

    async def all_org_ids(self) -> list[UUID]: ...


def _chunk_params(settings: Settings) -> ChunkParams:
    return ChunkParams(
        token_target=settings.chunk_token_target,
        token_overlap=settings.chunk_token_overlap,
        table_rows=settings.chunk_table_rows,
    )


def _build_pieces(parsed: ParsedDocument, params: ChunkParams) -> list[ChunkPiece]:
    """Chunk with the format-appropriate strategy (rules §4), de-duped by hash.

    Time: O(tokens) — single-pass strategy plus linear de-dup. Space: O(chunks).
    """
    strategy = select_chunking_strategy(parsed, params)
    return dedupe_by_hash(strategy.chunk(parsed))


async def _embed_and_store(
    chunks: ChunkRepository,
    embedder: Embedder,
    org_id: UUID,
    document_id: UUID,
    pieces: list[ChunkPiece],
    existing: set[str],
    batch_size: int,
) -> None:
    """Embed the not-yet-stored chunks in batches and bulk-upsert them.

    Only pieces whose content hash is not already stored are embedded, so a re-run
    on unchanged content makes zero provider calls and inserts zero rows. At most
    two batches are embedded concurrently (bounded concurrency) and results are
    consumed in order, so peak memory is O(batch) regardless of document size;
    each batch is one bulk INSERT, giving O(batches) statements. Documents reach
    READY only after this returns, so a READY document never has null embeddings.

    Time: O(chunks) work + O(batches) provider/DB round-trips. Space: O(batch).
    """
    indexed = [(seq, p) for seq, p in enumerate(pieces) if p.content_hash not in existing]
    batches = [indexed[start : start + batch_size] for start in range(0, len(indexed), batch_size)]
    semaphore = asyncio.Semaphore(2)

    async def embed_batch(batch: list[tuple[int, ChunkPiece]]) -> list[Vector]:
        async with semaphore:
            return await embedder.embed([piece.content for _, piece in batch])

    tasks = [asyncio.create_task(embed_batch(batch)) for batch in batches]
    for batch, task in zip(batches, tasks, strict=True):
        vectors = await task
        rows = [
            _row(org_id, document_id, seq, piece, vector)
            for (seq, piece), vector in zip(batch, vectors, strict=True)
        ]
        await chunks.upsert_embedded(rows)


def _row(
    org_id: UUID, document_id: UUID, seq: int, piece: ChunkPiece, embedding: Vector
) -> dict[str, Any]:
    return {
        "org_id": org_id,
        "document_id": document_id,
        "seq": seq,
        "content": piece.content,
        "content_hash": piece.content_hash,
        "token_count": piece.token_count,
        "chunk_metadata": dict(piece.metadata),
        "embedding": embedding,
    }


async def _read_object(storage: ObjectStorage, key: str) -> bytes:
    return b"".join([chunk async for chunk in storage.get_stream(key)])


async def ingest_document(ctx: dict[str, Any], document_id: str, org_id: str) -> None:
    """Claim, parse, chunk, and mark a document READY (idempotent)."""
    doc_id, oid = UUID(document_id), UUID(org_id)
    maker: SessionMaker = ctx["sessionmaker"]
    storage: ObjectStorage = ctx["storage"]
    settings: Settings = ctx["settings"]
    registry: ParserRegistry = ctx["registry"]
    embedder: Embedder = ctx["embedder"]

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
        supersedes_id = document.supersedes_id
        try:
            if settings.ingest_chaos_delay_seconds > 0:
                await asyncio.sleep(settings.ingest_chaos_delay_seconds)
            data = await _read_object(storage, storage_key)
            parsed = registry.parse(content_type, data, max_pages=settings.max_document_pages)
            pieces = _build_pieces(parsed, _chunk_params(settings))
            existing = await chunks.hashes_for_document(oid, doc_id)
            await _embed_and_store(
                chunks, embedder, oid, doc_id, pieces, existing, settings.embedding_batch_size
            )
            # Re-index cleanup: drop chunks whose hash this rebuild no longer produced.
            await chunks.delete_stale(oid, doc_id, {piece.content_hash for piece in pieces})
            await documents.mark_ready(oid, doc_id, page_count=parsed.page_count)
            if supersedes_id is not None:
                # New version is READY: retire the prior one so retrieval sees one.
                await chunks.delete_for_document(oid, supersedes_id)
                await documents.mark_superseded(oid, supersedes_id)
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
    make_tenant_lister = ctx["tenant_lister_factory"]
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.ingest_stuck_after_seconds)
    async with maker() as session:
        lister: TenantLister = make_tenant_lister(session)
        org_ids = await lister.all_org_ids()

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


async def sweep_storage_outbox(ctx: dict[str, Any]) -> None:
    """Delete storage objects recorded in the deletion outbox, then clear the rows.

    The compensation half of cascade delete: a row persists whenever the delete's
    after-commit storage call failed (or never ran), so retrying here guarantees no
    object is orphaned. Storage delete is idempotent, so a row processed twice is
    harmless. Time: O(pending rows). Space: O(1) per row.
    """
    maker: SessionMaker = ctx["sessionmaker"]
    storage: ObjectStorage = ctx["storage"]
    make_tenant_lister = ctx["tenant_lister_factory"]
    async with maker() as session:
        lister: TenantLister = make_tenant_lister(session)
        org_ids = await lister.all_org_ids()

    for oid in org_ids:
        async with maker() as session:
            await set_tenant_context(session, oid)
            outbox = StorageOutboxRepository(session)
            for row in await outbox.list_pending(oid):
                try:
                    await storage.delete(row.storage_key)
                except Exception:
                    await outbox.bump_attempts(oid, row.id)
                    _logger.exception("storage_outbox.delete_failed", outbox_id=str(row.id))
                else:
                    await outbox.delete(oid, row.id)
            await session.commit()


async def ingest_startup(ctx: dict[str, Any]) -> None:
    """Populate the arq ctx with knowledge's shared resources (on_startup hook).

    The cross-domain embedder is wired by the composition root (src/worker.py),
    not here — knowledge depends only on the Embedder Protocol via ``ctx``.
    """
    from src.knowledge.parsing.ocr_tesseract import TesseractOcrEngine

    ctx["sessionmaker"] = get_sessionmaker()
    ctx["storage"] = get_object_storage()
    ctx["settings"] = get_settings()
    ctx["registry"] = ParserRegistry(TesseractOcrEngine())
