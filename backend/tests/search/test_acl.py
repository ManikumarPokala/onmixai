"""Permission-aware retrieval zero-leak: a user without access to a collection gets
zero of its chunks — by search, by metadata-filter abuse, and by direct chunk id."""

from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.models import Role, User
from src.knowledge.models import Chunk, Collection, CollectionPermission, Document, DocumentStatus
from src.knowledge.repository import ChunkRepository
from src.knowledge.service import ChunkRetrievalService
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.fakes.fake_embedder import FakeEmbedder
from tests.search.conftest import SearchHarness
from tests.search.test_search_api import _auth, _collection, _login


async def _seed_chunk(
    session: AsyncSession,
    embedder: FakeEmbedder,
    *,
    org_id: UUID,
    user_id: UUID,
    collection_id: UUID,
    content: str,
) -> UUID:
    document_id, chunk_id = uuid4(), uuid4()
    await set_tenant_context(session, org_id)
    session.add(
        Document(
            id=document_id,
            org_id=org_id,
            collection_id=collection_id,
            filename="d.txt",
            content_type="text/plain",
            size_bytes=50,
            storage_key=f"org/{org_id}/doc/{document_id}",
            content_hash=f"{document_id}-h",
            status=DocumentStatus.READY,
            created_by=user_id,
        )
    )
    await session.flush()
    session.add(
        Chunk(
            id=chunk_id,
            org_id=org_id,
            document_id=document_id,
            seq=0,
            content=content,
            content_hash=f"{chunk_id}-h",
            token_count=len(content.split()),
            chunk_metadata={"page": 1},
            embedding=embedder._vector(content),
        )
    )
    await session.flush()
    return chunk_id


async def _seed_forbidden_collection(
    session: AsyncSession, embedder: FakeEmbedder, *, org_id: UUID, content: str
) -> tuple[UUID, UUID]:
    """A collection in the same org owned by a different user — the actor has no
    permission row on it. Returns (collection_id, chunk_id)."""
    other_user, collection_id = uuid4(), uuid4()
    await set_tenant_context(session, org_id)
    session.add(
        User(
            id=other_user,
            org_id=org_id,
            email=f"other-{other_user}@x.test",
            password_hash="x",
            full_name="Other",
            role=Role.MEMBER,
        )
    )
    await session.flush()
    session.add(
        Collection(id=collection_id, org_id=org_id, name="forbidden", created_by=other_user)
    )
    await session.flush()
    session.add(
        CollectionPermission(
            org_id=org_id, collection_id=collection_id, user_id=other_user, permission="manage"
        )
    )
    chunk_id = await _seed_chunk(
        session,
        embedder,
        org_id=org_id,
        user_id=other_user,
        collection_id=collection_id,
        content=content,
    )
    return collection_id, chunk_id


async def test_search_excludes_documents_user_cannot_access(
    search_harness: SearchHarness, db_session: AsyncSession
) -> None:
    token, org_id, user_id = await _login(search_harness, f"acl-{uuid4().hex[:8]}")
    mine = await _collection(search_harness, token, "Mine")
    accessible = await _seed_chunk(
        db_session,
        search_harness.embedder,
        org_id=org_id,
        user_id=user_id,
        collection_id=mine,
        content="quarterly revenue secret alpha",
    )
    _forbidden_coll, forbidden_chunk = await _seed_forbidden_collection(
        db_session,
        search_harness.embedder,
        org_id=org_id,
        content="quarterly revenue secret alpha",
    )
    body = (
        await search_harness.client.post(
            "/api/v1/search", headers=_auth(token), json={"query": "quarterly revenue", "limit": 50}
        )
    ).json()
    ids = {r["chunk_id"] for r in body["results"]}
    assert str(accessible) in ids  # own collection surfaces
    assert str(forbidden_chunk) not in ids  # no-permission collection never leaks


async def test_metadata_filter_abuse_returns_nothing(
    search_harness: SearchHarness, db_session: AsyncSession
) -> None:
    token, org_id, _user = await _login(search_harness, f"acl-{uuid4().hex[:8]}")
    forbidden_coll, _chunk = await _seed_forbidden_collection(
        db_session, search_harness.embedder, org_id=org_id, content="restricted alpha"
    )
    # Naming the forbidden collection as a filter must not widen the ACL.
    body = (
        await search_harness.client.post(
            "/api/v1/search",
            headers=_auth(token),
            json={"query": "restricted alpha", "collection_id": str(forbidden_coll)},
        )
    ).json()
    assert body["results"] == []


async def test_direct_chunk_id_is_acl_filtered(
    search_harness: SearchHarness, db_session: AsyncSession, settings: Settings
) -> None:
    token, org_id, user_id = await _login(search_harness, f"acl-{uuid4().hex[:8]}")
    mine = await _collection(search_harness, token, "Mine")
    accessible = await _seed_chunk(
        db_session,
        search_harness.embedder,
        org_id=org_id,
        user_id=user_id,
        collection_id=mine,
        content="accessible alpha",
    )
    _coll, forbidden_chunk = await _seed_forbidden_collection(
        db_session, search_harness.embedder, org_id=org_id, content="forbidden alpha"
    )
    await set_tenant_context(db_session, org_id)
    service = ChunkRetrievalService(ChunkRepository(db_session), settings)
    got = await service.candidates_by_ids(org_id, user_id, [accessible, forbidden_chunk])
    returned = {c.chunk_id for c in got}
    assert accessible in returned  # the actor's own chunk hydrates
    assert forbidden_chunk not in returned  # a known forbidden id returns nothing
