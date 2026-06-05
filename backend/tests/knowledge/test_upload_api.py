"""API + service tests for the streaming, quota-enforced upload."""

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.models import Role
from src.identity.schemas import AuthContext
from src.knowledge.exceptions import DocumentQuotaExceededError, UploadTooLargeError
from src.knowledge.repository import CollectionRepository, DocumentRepository
from src.knowledge.service import KnowledgeService, OrgQuotaReader
from src.shared.audit import AuditEmitter
from src.shared.config import Settings
from tests.fakes.fake_queue import FakeJobQueue
from tests.fakes.fake_storage import FakeObjectStorage
from tests.knowledge.conftest import UPLOAD_LIMIT, KnowledgeHarness, register_and_login


class _FakeQuota:
    """Stand-in OrgQuotaReader proving the quota path goes through the interface."""

    def __init__(self, quota: int) -> None:
        self.quota = quota
        self.calls = 0

    async def get_document_quota(self, org_id: UUID) -> int:
        self.calls += 1
        return self.quota


def _service(
    db_session: AsyncSession,
    settings: Settings,
    *,
    quota: OrgQuotaReader,
    storage: FakeObjectStorage | None = None,
) -> KnowledgeService:
    return KnowledgeService(
        session=db_session,
        collections=CollectionRepository(db_session),
        documents=DocumentRepository(db_session),
        storage=storage or FakeObjectStorage(),
        queue=FakeJobQueue(),
        audit=AuditEmitter(),
        quota_reader=quota,
        settings=settings,
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _new_collection(harness: KnowledgeHarness, token: str) -> str:
    created = await harness.client.post(
        "/api/v1/collections", json={"name": "Docs"}, headers=_auth(token)
    )
    return str(created.json()["id"])


async def test_upload_happy_path_queues_and_stores(harness: KnowledgeHarness) -> None:
    token = await register_and_login(harness.client, slug="acme", email="o@acme.test")
    collection_id = await _new_collection(harness, token)

    upload = await harness.client.post(
        f"/api/v1/collections/{collection_id}/documents",
        files={"file": ("doc.txt", b"hello world", "text/plain")},
        headers=_auth(token),
    )
    assert upload.status_code == 202
    body = upload.json()
    assert body["status"] == "queued"
    document_id = body["document_id"]

    assert len(harness.storage.objects) == 1  # object streamed to storage
    assert len(harness.queue.enqueued) == 1  # enqueued only after commit
    assert str(harness.queue.enqueued[0][0]) == document_id

    polled = await harness.client.get(f"/api/v1/documents/{document_id}", headers=_auth(token))
    assert polled.status_code == 200
    assert polled.json()["status"] == "queued"


async def test_unsupported_format_rejected(harness: KnowledgeHarness) -> None:
    token = await register_and_login(harness.client, slug="acme", email="o@acme.test")
    collection_id = await _new_collection(harness, token)
    upload = await harness.client.post(
        f"/api/v1/collections/{collection_id}/documents",
        files={"file": ("x.png", b"\x89PNG", "image/png")},
        headers=_auth(token),
    )
    assert upload.status_code == 415
    assert upload.json()["error"]["code"] == "UNSUPPORTED_FORMAT"
    assert harness.storage.objects == {}


async def test_oversize_upload_rejected_no_orphan(harness: KnowledgeHarness) -> None:
    token = await register_and_login(harness.client, slug="acme", email="o@acme.test")
    collection_id = await _new_collection(harness, token)
    upload = await harness.client.post(
        f"/api/v1/collections/{collection_id}/documents",
        files={"file": ("big.txt", b"x" * (UPLOAD_LIMIT + 2048), "text/plain")},
        headers=_auth(token),
    )
    assert upload.status_code == 413
    assert upload.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
    assert harness.storage.objects == {}  # no orphan object remains


async def test_quota_enforced(harness: KnowledgeHarness, db_session: AsyncSession) -> None:
    token = await register_and_login(harness.client, slug="acme", email="o@acme.test")
    collection_id = await _new_collection(harness, token)
    org_id = (await harness.client.get("/api/v1/users/me", headers=_auth(token))).json()["org_id"]
    await db_session.execute(
        text("UPDATE organizations SET max_documents = 1 WHERE id = :id"), {"id": org_id}
    )

    first = await harness.client.post(
        f"/api/v1/collections/{collection_id}/documents",
        files={"file": ("a.txt", b"a", "text/plain")},
        headers=_auth(token),
    )
    assert first.status_code == 202
    second = await harness.client.post(
        f"/api/v1/collections/{collection_id}/documents",
        files={"file": ("b.txt", b"b", "text/plain")},
        headers=_auth(token),
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "DOCUMENT_QUOTA_EXCEEDED"


async def test_mid_stream_oversize_aborts_storage(
    harness: KnowledgeHarness, db_session: AsyncSession, settings: Settings
) -> None:
    # Drive the service directly with declared_size=0 (bypassing the Content-Length
    # pre-check) so the mid-stream cap + storage abort are exercised: no orphan.
    token = await register_and_login(harness.client, slug="acme", email="o@acme.test")
    me = (await harness.client.get("/api/v1/users/me", headers=_auth(token))).json()
    actor = AuthContext(user_id=UUID(me["id"]), org_id=UUID(me["org_id"]), role=Role.OWNER)

    storage = FakeObjectStorage()
    service = _service(
        db_session,
        settings.model_copy(update={"max_upload_bytes": 10}),
        quota=_FakeQuota(500),
        storage=storage,
    )
    collection = await service.create_collection(actor, name="Direct", description=None)

    async def _oversize() -> AsyncIterator[bytes]:
        yield b"x" * 100

    with pytest.raises(UploadTooLargeError):
        await service.upload_document(
            actor,
            collection_id=collection.id,
            filename="big.txt",
            content_type="text/plain",
            declared_size=0,
            source=_oversize(),
        )
    assert storage.objects == {}


async def test_quota_path_goes_through_injected_reader(
    harness: KnowledgeHarness, db_session: AsyncSession, settings: Settings
) -> None:
    # Proves the quota comes from the OrgQuotaReader interface, not a knowledge
    # query of identity's table: a fake reader with quota=1 blocks the 2nd upload.
    token = await register_and_login(harness.client, slug="acme", email="o@acme.test")
    me = (await harness.client.get("/api/v1/users/me", headers=_auth(token))).json()
    actor = AuthContext(user_id=UUID(me["id"]), org_id=UUID(me["org_id"]), role=Role.OWNER)

    quota = _FakeQuota(1)
    service = _service(db_session, settings, quota=quota)
    collection = await service.create_collection(actor, name="Quota", description=None)

    async def _one_byte() -> AsyncIterator[bytes]:
        yield b"a"

    await service.upload_document(
        actor,
        collection_id=collection.id,
        filename="a.txt",
        content_type="text/plain",
        declared_size=1,
        source=_one_byte(),
    )
    with pytest.raises(DocumentQuotaExceededError):
        await service.upload_document(
            actor,
            collection_id=collection.id,
            filename="b.txt",
            content_type="text/plain",
            declared_size=1,
            source=_one_byte(),
        )
    assert quota.calls >= 2  # the quota was read through the interface each upload
