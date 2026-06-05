"""Worker tests: CAS claim, TXT pipeline, retry, sweeper, idempotency.

The worker uses its own committed sessions, so these tests seed committed data
(unique org per test, so leftovers are harmless) rather than the rolled-back
db_session used by API tests.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from src.identity.models import Organization, Role, User
from src.identity.repository import OrganizationRepository
from src.identity.service import OrgPolicyService
from src.knowledge.models import Collection, Document, DocumentStatus
from src.knowledge.repository import ChunkRepository, DocumentRepository
from src.knowledge.worker import RetryableError, ingest_document, sweep_stuck_documents
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.fakes.fake_storage import FakeObjectStorage


async def _seed_document(
    engine: AsyncEngine, *, content: bytes = b"hello world " * 200, content_type: str = "text/plain"
) -> tuple[UUID, UUID, str]:
    """Commit a fresh org/user/collection/QUEUED-document; return ids + storage key."""
    org_id, user_id, collection_id, document_id = uuid4(), uuid4(), uuid4(), uuid4()
    key = f"org/{org_id}/doc/{document_id}"
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_context(session, org_id)
        session.add(Organization(id=org_id, name="W", slug=f"w-{org_id}"))
        session.add(
            User(
                id=user_id,
                org_id=org_id,
                email=f"u-{user_id}@w.test",
                password_hash="x",
                full_name="U",
                role=Role.OWNER,
            )
        )
        await session.flush()
        session.add(Collection(id=collection_id, org_id=org_id, name="C", created_by=user_id))
        await session.flush()
        session.add(
            Document(
                id=document_id,
                org_id=org_id,
                collection_id=collection_id,
                filename="f.txt",
                content_type=content_type,
                size_bytes=len(content),
                storage_key=key,
                content_hash="0" * 64,
                status=DocumentStatus.QUEUED,
                created_by=user_id,
            )
        )
        await session.commit()
    return org_id, document_id, key


def _ctx(engine: AsyncEngine, storage: FakeObjectStorage, settings: Settings) -> dict[str, object]:
    return {
        "sessionmaker": async_sessionmaker(engine, expire_on_commit=False),
        "storage": storage,
        "settings": settings,
        "redis": None,
        "tenant_lister_factory": lambda s: OrgPolicyService(OrganizationRepository(s)),
    }


async def _status(engine: AsyncEngine, org_id: UUID, document_id: UUID) -> Document:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_context(session, org_id)
        document = await DocumentRepository(session).get(org_id, document_id)
        assert document is not None
        return document


async def test_claim_is_compare_and_set(app_engine: AsyncEngine) -> None:
    org_id, document_id, _ = await _seed_document(app_engine)
    maker = async_sessionmaker(app_engine, expire_on_commit=False)

    async def _claim() -> bool:
        async with maker() as session:
            await set_tenant_context(session, org_id)
            won = await DocumentRepository(session).claim_for_processing(
                org_id, document_id, datetime.now(UTC)
            )
            await session.commit()
            return won

    results = await asyncio.gather(_claim(), _claim())
    assert sum(results) == 1  # exactly one worker wins the claim


async def test_txt_end_to_end_reaches_ready_with_chunks(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    org_id, document_id, key = await _seed_document(app_engine)
    storage = FakeObjectStorage()
    storage.objects[key] = b"first paragraph. " * 100

    await ingest_document(_ctx(app_engine, storage, settings), str(document_id), str(org_id))

    document = await _status(app_engine, org_id, document_id)
    assert document.status == DocumentStatus.READY
    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_context(session, org_id)
        assert await ChunkRepository(session).count_for_document(org_id, document_id) > 0


async def test_duplicate_delivery_processes_once(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    org_id, document_id, key = await _seed_document(app_engine)
    storage = FakeObjectStorage()
    storage.objects[key] = b"content " * 50
    ctx = _ctx(app_engine, storage, settings)

    await asyncio.gather(
        ingest_document(ctx, str(document_id), str(org_id)),
        ingest_document(ctx, str(document_id), str(org_id)),
    )
    document = await _status(app_engine, org_id, document_id)
    assert document.status == DocumentStatus.READY
    assert document.attempt_count == 1  # claimed exactly once


async def test_retry_then_terminal_failure(app_engine: AsyncEngine, settings: Settings) -> None:
    org_id, document_id, key = await _seed_document(app_engine)

    class _TransientStorage(FakeObjectStorage):
        async def get_stream(self, key: str) -> AsyncIterator[bytes]:
            raise RetryableError("transient storage outage")
            if False:  # pragma: no cover - makes this an async generator
                yield b""

    ctx = _ctx(
        app_engine, _TransientStorage(), settings.model_copy(update={"ingest_max_attempts": 2})
    )

    await ingest_document(ctx, str(document_id), str(org_id))
    after_first = await _status(app_engine, org_id, document_id)
    assert after_first.status == DocumentStatus.QUEUED
    assert after_first.attempt_count == 1

    await ingest_document(ctx, str(document_id), str(org_id))
    after_second = await _status(app_engine, org_id, document_id)
    assert after_second.status == DocumentStatus.FAILED
    assert after_second.failure_reason


async def test_sweeper_requeues_stale_and_caps(app_engine: AsyncEngine, settings: Settings) -> None:
    org_id, document_id, key = await _seed_document(app_engine)
    storage = FakeObjectStorage()
    storage.objects[key] = b"content"
    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    stale = datetime.now(UTC) - timedelta(hours=1)
    # Force a stale PROCESSING claim (as if a worker died mid-task).
    async with maker() as session:
        await set_tenant_context(session, org_id)
        await session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(status=DocumentStatus.PROCESSING, claimed_at=stale, attempt_count=1)
        )
        await session.commit()

    sweep_ctx = _ctx(
        app_engine, storage, settings.model_copy(update={"ingest_stuck_after_seconds": 0})
    )
    await sweep_stuck_documents(sweep_ctx)
    assert (await _status(app_engine, org_id, document_id)).status == DocumentStatus.QUEUED

    # A stale claim at the attempt cap is failed, not requeued forever.
    async with maker() as session:
        await set_tenant_context(session, org_id)
        await session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(status=DocumentStatus.PROCESSING, claimed_at=stale, attempt_count=3)
        )
        await session.commit()
    await sweep_stuck_documents(sweep_ctx)
    assert (await _status(app_engine, org_id, document_id)).status == DocumentStatus.FAILED


async def test_reprocess_yields_identical_chunk_set(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    org_id, document_id, key = await _seed_document(app_engine)
    storage = FakeObjectStorage()
    storage.objects[key] = b"deterministic content. " * 300
    ctx = _ctx(app_engine, storage, settings)
    maker = async_sessionmaker(app_engine, expire_on_commit=False)

    await ingest_document(ctx, str(document_id), str(org_id))
    async with maker() as session:
        await set_tenant_context(session, org_id)
        first = await ChunkRepository(session).hashes_for_document(org_id, document_id)

    # Re-queue (ready -> queued) and reprocess: identical chunk hashes (idempotent).
    async with maker() as session:
        await set_tenant_context(session, org_id)
        await session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(status=DocumentStatus.QUEUED, claimed_at=None)
        )
        await session.commit()
    await ingest_document(ctx, str(document_id), str(org_id))
    async with maker() as session:
        await set_tenant_context(session, org_id)
        second = await ChunkRepository(session).hashes_for_document(org_id, document_id)

    assert first == second and len(first) > 0
