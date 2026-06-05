"""Document lifecycle: versioning + supersede, cascade delete with storage
compensation, re-index, and empty-only collection delete. Real Postgres; the
worker runs inline with fakes."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from src.identity.models import Organization, Role, User
from src.identity.repository import OrganizationRepository
from src.identity.schemas import AuthContext
from src.identity.service import OrgPolicyService
from src.knowledge.exceptions import (
    CollectionNotEmptyError,
    DocumentProcessingError,
    InvalidStatusTransitionError,
)
from src.knowledge.models import (
    Chunk,
    Collection,
    CollectionPermission,
    Document,
    DocumentStatus,
    Permission,
    StorageDeletionOutbox,
)
from src.knowledge.parsing.registry import ParserRegistry
from src.knowledge.repository import (
    ChunkRepository,
    CollectionRepository,
    DocumentRepository,
    StorageOutboxRepository,
)
from src.knowledge.service import KnowledgeService
from src.knowledge.worker import ingest_document, sweep_storage_outbox
from src.shared.config import Settings
from src.shared.database import run_after_commit, set_tenant_context
from tests.fakes.fake_embedder import FakeEmbedder
from tests.fakes.fake_ocr import FakeOcrEngine
from tests.fakes.fake_queue import FakeJobQueue
from tests.fakes.fake_storage import FakeObjectStorage


class _SpyAudit:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def emit(self, *, action: str, **_: object) -> None:
        self.actions.append(action)


@dataclass
class _Fixture:
    org_id: UUID
    actor: AuthContext
    collection_id: UUID
    document_id: UUID
    storage_key: str


async def _seed(engine: AsyncEngine, *, content: bytes, status: DocumentStatus) -> _Fixture:
    org_id, user_id, collection_id, document_id = uuid4(), uuid4(), uuid4(), uuid4()
    key = f"org/{org_id}/doc/{document_id}"
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_context(session, org_id)
        session.add(Organization(id=org_id, name="L", slug=f"l-{org_id}"))
        session.add(
            User(
                id=user_id,
                org_id=org_id,
                email=f"u-{user_id}@l.test",
                password_hash="x",
                full_name="U",
                role=Role.OWNER,
            )
        )
        await session.flush()
        session.add(Collection(id=collection_id, org_id=org_id, name="C", created_by=user_id))
        await session.flush()
        session.add(
            CollectionPermission(
                org_id=org_id,
                collection_id=collection_id,
                user_id=user_id,
                permission=Permission.MANAGE,
            )
        )
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
                status=status,
                created_by=user_id,
            )
        )
        await session.commit()
    return _Fixture(
        org_id=org_id,
        actor=AuthContext(user_id=user_id, org_id=org_id, role=Role.OWNER),
        collection_id=collection_id,
        document_id=document_id,
        storage_key=key,
    )


def _ctx(engine: AsyncEngine, storage: FakeObjectStorage, settings: Settings) -> dict[str, object]:
    return {
        "sessionmaker": async_sessionmaker(engine, expire_on_commit=False),
        "storage": storage,
        "settings": settings,
        "redis": None,
        "registry": ParserRegistry(FakeOcrEngine()),
        "embedder": FakeEmbedder(settings.embedding_dimension),
        "tenant_lister_factory": lambda s: OrgPolicyService(OrganizationRepository(s)),
    }


def _service(
    session: object,
    storage: FakeObjectStorage,
    queue: FakeJobQueue,
    audit: _SpyAudit,
    settings: Settings,
) -> KnowledgeService:
    return KnowledgeService(
        session=session,  # type: ignore[arg-type]
        collections=CollectionRepository(session),  # type: ignore[arg-type]
        documents=DocumentRepository(session),  # type: ignore[arg-type]
        chunks=ChunkRepository(session),  # type: ignore[arg-type]
        outbox=StorageOutboxRepository(session),  # type: ignore[arg-type]
        storage=storage,
        queue=queue,
        audit=audit,  # type: ignore[arg-type]
        quota_reader=OrgPolicyService(OrganizationRepository(session)),  # type: ignore[arg-type]
        settings=settings,
    )


async def _source(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _ingest(
    engine: AsyncEngine,
    storage: FakeObjectStorage,
    settings: Settings,
    fx: _Fixture,
    document_id: UUID,
) -> None:
    await ingest_document(_ctx(engine, storage, settings), str(document_id), str(fx.org_id))


async def _chunk_count(engine: AsyncEngine, org_id: UUID, document_id: UUID) -> int:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_context(session, org_id)
        stmt = select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id)
        return int((await session.execute(stmt)).scalar_one())


async def _get_document(engine: AsyncEngine, org_id: UUID, document_id: UUID) -> Document:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_context(session, org_id)
        document = await DocumentRepository(session).get(org_id, document_id)
        assert document is not None
        return document


async def test_version_chain_supersedes_prior(app_engine: AsyncEngine, settings: Settings) -> None:
    storage = FakeObjectStorage()
    fx = await _seed(app_engine, content=b"alpha beta. gamma delta.", status=DocumentStatus.QUEUED)
    storage.objects[fx.storage_key] = b"alpha beta. gamma delta."
    await _ingest(app_engine, storage, settings, fx, fx.document_id)
    assert await _chunk_count(app_engine, fx.org_id, fx.document_id) > 0

    # Upload a new version through the service (version chain + supersedes link).
    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    queue = FakeJobQueue()
    async with maker() as session:
        await set_tenant_context(session, fx.org_id)
        service = _service(session, storage, queue, _SpyAudit(), settings)
        accepted = await service.create_version(
            fx.actor,
            document_id=fx.document_id,
            filename="f2.txt",
            content_type="text/plain",
            declared_size=24,
            source=_source(b"epsilon zeta. eta theta."),
        )
        await session.commit()
        await run_after_commit(session)
    version_id = accepted.document_id

    v2 = await _get_document(app_engine, fx.org_id, version_id)
    assert v2.version == 2 and v2.supersedes_id == fx.document_id

    await _ingest(app_engine, storage, settings, fx, version_id)

    # After v2 is READY: v1 has no chunks and is flagged superseded; v2 is live.
    assert await _chunk_count(app_engine, fx.org_id, fx.document_id) == 0
    assert await _chunk_count(app_engine, fx.org_id, version_id) > 0
    assert (await _get_document(app_engine, fx.org_id, fx.document_id)).superseded is True
    assert (await _get_document(app_engine, fx.org_id, version_id)).superseded is False


async def test_delete_removes_chunks_object_and_audits(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    storage = FakeObjectStorage()
    fx = await _seed(app_engine, content=b"to be deleted. soon gone.", status=DocumentStatus.QUEUED)
    storage.objects[fx.storage_key] = b"to be deleted. soon gone."
    await _ingest(app_engine, storage, settings, fx, fx.document_id)
    assert await _chunk_count(app_engine, fx.org_id, fx.document_id) > 0

    audit = _SpyAudit()
    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_context(session, fx.org_id)
        service = _service(session, storage, FakeJobQueue(), audit, settings)
        await service.delete_document(fx.actor, fx.document_id)
        await session.commit()
        await run_after_commit(session)  # fires the best-effort storage delete

    assert await _chunk_count(app_engine, fx.org_id, fx.document_id) == 0
    assert fx.storage_key not in storage.objects  # object removed
    assert "document.deleted" in audit.actions


async def test_delete_during_processing_conflicts(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    storage = FakeObjectStorage()
    fx = await _seed(app_engine, content=b"busy", status=DocumentStatus.PROCESSING)
    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_context(session, fx.org_id)
        service = _service(session, storage, FakeJobQueue(), _SpyAudit(), settings)
        with pytest.raises(DocumentProcessingError) as exc:
            await service.delete_document(fx.actor, fx.document_id)
        assert exc.value.code == "DOCUMENT_PROCESSING"


async def test_reindex_rebuilds_identical_hashes(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    storage = FakeObjectStorage()
    fx = await _seed(
        app_engine, content=b"stable content. stays same.", status=DocumentStatus.QUEUED
    )
    storage.objects[fx.storage_key] = b"stable content. stays same."
    await _ingest(app_engine, storage, settings, fx, fx.document_id)
    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_context(session, fx.org_id)
        before = await ChunkRepository(session).hashes_for_document(fx.org_id, fx.document_id)

    async with maker() as session:
        await set_tenant_context(session, fx.org_id)
        service = _service(session, storage, FakeJobQueue(), _SpyAudit(), settings)
        await service.reindex_document(fx.actor, fx.document_id)
        await session.commit()
        await run_after_commit(session)

    assert (
        await _get_document(app_engine, fx.org_id, fx.document_id)
    ).status == DocumentStatus.QUEUED
    await _ingest(app_engine, storage, settings, fx, fx.document_id)

    async with maker() as session:
        await set_tenant_context(session, fx.org_id)
        after = await ChunkRepository(session).hashes_for_document(fx.org_id, fx.document_id)
    assert (
        await _get_document(app_engine, fx.org_id, fx.document_id)
    ).status == DocumentStatus.READY
    assert after == before and len(after) > 0


async def test_reindex_requires_ready(app_engine: AsyncEngine, settings: Settings) -> None:
    storage = FakeObjectStorage()
    fx = await _seed(app_engine, content=b"queued", status=DocumentStatus.QUEUED)
    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_context(session, fx.org_id)
        service = _service(session, storage, FakeJobQueue(), _SpyAudit(), settings)
        with pytest.raises(InvalidStatusTransitionError):
            await service.reindex_document(fx.actor, fx.document_id)


async def test_collection_delete_requires_empty(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    storage = FakeObjectStorage()
    fx = await _seed(app_engine, content=b"x", status=DocumentStatus.QUEUED)
    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_context(session, fx.org_id)
        service = _service(session, storage, FakeJobQueue(), _SpyAudit(), settings)
        with pytest.raises(CollectionNotEmptyError):
            await service.delete_collection(fx.actor, fx.collection_id)

    # Remove the document, then the collection deletes cleanly.
    async with maker() as session:
        await set_tenant_context(session, fx.org_id)
        await DocumentRepository(session).delete(fx.org_id, fx.document_id)
        await session.commit()
    async with maker() as session:
        await set_tenant_context(session, fx.org_id)
        service = _service(session, storage, FakeJobQueue(), _SpyAudit(), settings)
        await service.delete_collection(fx.actor, fx.collection_id)
        await session.commit()
    async with maker() as session:
        await set_tenant_context(session, fx.org_id)
        assert await CollectionRepository(session).get(fx.org_id, fx.collection_id) is None


async def test_storage_compensation_outbox_empties_orphan(
    app_engine: AsyncEngine, settings: Settings
) -> None:
    storage = FakeObjectStorage()
    fx = await _seed(
        app_engine, content=b"orphan candidate. body here.", status=DocumentStatus.QUEUED
    )
    storage.objects[fx.storage_key] = b"orphan candidate. body here."
    await _ingest(app_engine, storage, settings, fx, fx.document_id)

    # Delete with storage failing: the object is left behind but the outbox records it.
    storage.fail_delete = True
    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_context(session, fx.org_id)
        service = _service(session, storage, FakeJobQueue(), _SpyAudit(), settings)
        await service.delete_document(fx.actor, fx.document_id)
        await session.commit()
        await run_after_commit(session)  # storage delete raises, is swallowed

    assert fx.storage_key in storage.objects  # orphan still present
    async with maker() as session:
        await set_tenant_context(session, fx.org_id)
        pending = await StorageOutboxRepository(session).list_pending(fx.org_id)
        assert len(pending) == 1 and pending[0].storage_key == fx.storage_key

    # Storage recovers; the sweeper deletes the object and clears the outbox.
    storage.fail_delete = False
    await sweep_storage_outbox(_ctx(app_engine, storage, settings))

    assert fx.storage_key not in storage.objects  # no orphan remains
    async with maker() as session:
        await set_tenant_context(session, fx.org_id)
        remaining = (
            await session.execute(
                select(func.count())
                .select_from(StorageDeletionOutbox)
                .where(StorageDeletionOutbox.org_id == fx.org_id)
            )
        ).scalar_one()
        assert remaining == 0
