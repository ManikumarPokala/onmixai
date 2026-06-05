"""Embedding pipeline (worker step): batched, idempotent, bounded-statement upsert
with the READY ⇒ no-null-embeddings invariant. Real Postgres; fake embedder."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from src.ai.embedding import EmbeddingDimensionError, Vector
from src.identity.models import Organization, Role, User
from src.identity.repository import OrganizationRepository
from src.identity.service import OrgPolicyService
from src.knowledge.models import Chunk, Collection, Document, DocumentStatus
from src.knowledge.parsing.registry import ParserRegistry
from src.knowledge.repository import DocumentRepository
from src.knowledge.worker import ingest_document
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.fakes.fake_embedder import FakeEmbedder
from tests.fakes.fake_ocr import FakeOcrEngine
from tests.fakes.fake_storage import FakeObjectStorage


async def _seed(engine: AsyncEngine, content: bytes) -> tuple[UUID, UUID, str]:
    org_id, user_id, collection_id, document_id = uuid4(), uuid4(), uuid4(), uuid4()
    key = f"org/{org_id}/doc/{document_id}"
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_context(session, org_id)
        session.add(Organization(id=org_id, name="E", slug=f"e-{org_id}"))
        session.add(
            User(
                id=user_id,
                org_id=org_id,
                email=f"u-{user_id}@e.test",
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
                content_type="text/plain",
                size_bytes=len(content),
                storage_key=key,
                content_hash="0" * 64,
                status=DocumentStatus.QUEUED,
                created_by=user_id,
            )
        )
        await session.commit()
    return org_id, document_id, key


def _ctx(
    engine: AsyncEngine, storage: FakeObjectStorage, settings: Settings, embedder: object
) -> dict[str, object]:
    return {
        "sessionmaker": async_sessionmaker(engine, expire_on_commit=False),
        "storage": storage,
        "settings": settings,
        "redis": None,
        "registry": ParserRegistry(FakeOcrEngine()),
        "embedder": embedder,
        "tenant_lister_factory": lambda s: OrgPolicyService(OrganizationRepository(s)),
    }


async def _status(engine: AsyncEngine, org_id: UUID, document_id: UUID) -> Document:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_context(session, org_id)
        document = await DocumentRepository(session).get(org_id, document_id)
        assert document is not None
        return document


async def _scalar(engine: AsyncEngine, org_id: UUID, stmt: object) -> int:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_context(session, org_id)
        return int((await session.execute(stmt)).scalar_one())  # type: ignore[arg-type]


async def test_chunks_embed_in_batches_with_bounded_statement_count(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    # token_target=1 → one chunk per distinct token; 250 distinct tokens → 250 chunks.
    content = " ".join(f"w{i}" for i in range(250)).encode("utf-8")
    org_id, document_id, key = await _seed(app_engine, content)
    storage = FakeObjectStorage()
    storage.objects[key] = content
    tuned = settings.model_copy(
        update={"chunk_token_target": 1, "chunk_token_overlap": 0, "embedding_batch_size": 100}
    )
    embedder = FakeEmbedder(settings.embedding_dimension)

    inserts: list[str] = []

    def _count(conn: object, cursor: object, statement: str, *args: object) -> None:
        if "INSERT INTO chunks" in statement:
            inserts.append(statement)

    event.listen(app_engine.sync_engine, "before_cursor_execute", _count)
    try:
        await ingest_document(
            _ctx(app_engine, storage, tuned, embedder), str(document_id), str(org_id)
        )
    finally:
        event.remove(app_engine.sync_engine, "before_cursor_execute", _count)

    assert embedder.calls == 3  # ceil(250 / 100) batches
    assert len(inserts) == 3  # one bulk INSERT per batch — O(batches), not O(chunks)
    count = await _scalar(
        app_engine,
        org_id,
        select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id),
    )
    assert count == 250


async def test_reembedding_same_document_inserts_zero_rows(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    content = b"deterministic prose. " * 200
    org_id, document_id, key = await _seed(app_engine, content)
    storage = FakeObjectStorage()
    storage.objects[key] = content

    first = FakeEmbedder(settings.embedding_dimension)
    await ingest_document(_ctx(app_engine, storage, settings, first), str(document_id), str(org_id))
    after_first = await _scalar(
        app_engine,
        org_id,
        select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id),
    )
    assert first.calls > 0 and after_first > 0

    # Re-queue and reprocess with a fresh embedder: nothing new to embed or insert.
    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_context(session, org_id)
        await session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(status=DocumentStatus.QUEUED, claimed_at=None)
        )
        await session.commit()

    second = FakeEmbedder(settings.embedding_dimension)
    await ingest_document(
        _ctx(app_engine, storage, settings, second), str(document_id), str(org_id)
    )
    after_second = await _scalar(
        app_engine,
        org_id,
        select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id),
    )
    assert second.calls == 0  # every chunk already stored → no provider calls
    assert after_second == after_first  # zero new rows


async def test_ready_document_has_no_null_embeddings(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    content = b"first sentence here. second sentence follows. third one too. " * 20
    org_id, document_id, key = await _seed(app_engine, content)
    storage = FakeObjectStorage()
    storage.objects[key] = content

    await ingest_document(
        _ctx(app_engine, storage, settings, FakeEmbedder(settings.embedding_dimension)),
        str(document_id),
        str(org_id),
    )

    assert (await _status(app_engine, org_id, document_id)).status == DocumentStatus.READY
    nulls = await _scalar(
        app_engine,
        org_id,
        select(func.count())
        .select_from(Chunk)
        .where(Chunk.document_id == document_id, Chunk.embedding.is_(None)),
    )
    total = await _scalar(
        app_engine,
        org_id,
        select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id),
    )
    assert total > 0 and nulls == 0  # READY ⇒ every chunk embedded


async def test_dimension_mismatch_fails_the_document(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    class _MismatchEmbedder:
        async def embed(self, texts: list[str]) -> list[Vector]:
            raise EmbeddingDimensionError(
                settings.embedding_dimension, settings.embedding_dimension + 1
            )

    content = b"some content to chunk. and embed. " * 10
    org_id, document_id, key = await _seed(app_engine, content)
    storage = FakeObjectStorage()
    storage.objects[key] = content

    # A permanent error marks the document FAILED and re-raises so arq records it.
    with pytest.raises(EmbeddingDimensionError):
        await ingest_document(
            _ctx(app_engine, storage, settings, _MismatchEmbedder()), str(document_id), str(org_id)
        )

    document = await _status(app_engine, org_id, document_id)
    assert document.status == DocumentStatus.FAILED  # permanent, not retried
    assert document.failure_reason
