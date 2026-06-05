"""POST /api/v1/search — hybrid retrieval API: fused + attributed results, empty
result is 200, filters narrow, pagination caps, bad filter is 422, no query echo."""

from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.knowledge.models import Chunk, Document, DocumentStatus
from src.shared.database import set_tenant_context
from tests.fakes.fake_embedder import FakeEmbedder
from tests.search.conftest import SearchHarness


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(harness: SearchHarness, slug: str) -> tuple[str, UUID, UUID]:
    email = f"o@{slug}.test"
    await harness.client.post(
        "/api/v1/auth/register",
        json={
            "name": slug,
            "slug": slug,
            "owner_email": email,
            "password": "password-123456",
            "full_name": "O",
        },
    )
    token = (
        await harness.client.post(
            "/api/v1/auth/login",
            json={"org_slug": slug, "email": email, "password": "password-123456"},
        )
    ).json()["access_token"]
    me = (await harness.client.get("/api/v1/users/me", headers=_auth(token))).json()
    return token, UUID(me["org_id"]), UUID(me["id"])


async def _collection(harness: SearchHarness, token: str, name: str) -> UUID:
    return UUID(
        (
            await harness.client.post(
                "/api/v1/collections", headers=_auth(token), json={"name": name}
            )
        ).json()["id"]
    )


async def _seed_doc(
    session: AsyncSession,
    embedder: FakeEmbedder,
    *,
    org_id: UUID,
    user_id: UUID,
    collection_id: UUID,
    contents: list[str],
) -> UUID:
    document_id = uuid4()
    await set_tenant_context(session, org_id)
    session.add(
        Document(
            id=document_id,
            org_id=org_id,
            collection_id=collection_id,
            filename="doc.txt",
            content_type="text/plain",
            size_bytes=100,
            storage_key=f"org/{org_id}/doc/{document_id}",
            content_hash=f"{document_id}-h",
            status=DocumentStatus.READY,
            created_by=user_id,
        )
    )
    await session.flush()
    for i, content in enumerate(contents):
        session.add(
            Chunk(
                id=uuid4(),
                org_id=org_id,
                document_id=document_id,
                seq=i,
                content=content,
                content_hash=f"{document_id}-{i}",
                token_count=len(content.split()),
                chunk_metadata={"page": i + 1},
                embedding=embedder._vector(content),
            )
        )
    await session.flush()
    return document_id


async def test_search_returns_fused_attributed_results(
    search_harness: SearchHarness, db_session: AsyncSession
) -> None:
    token, org_id, user_id = await _login(search_harness, f"s-{uuid4().hex[:8]}")
    coll = await _collection(search_harness, token, "C")
    doc = await _seed_doc(
        db_session,
        search_harness.embedder,
        org_id=org_id,
        user_id=user_id,
        collection_id=coll,
        contents=["alpha beta gamma about retrieval", "wholly unrelated material"],
    )
    response = await search_harness.client.post(
        "/api/v1/search", headers=_auth(token), json={"query": "alpha beta", "limit": 10}
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"results", "next_cursor"}  # no query echo / leaked fields
    top = body["results"]
    assert any("alpha" in r["content"] for r in top)  # matched via the FTS arm
    hit = next(r for r in top if "alpha" in r["content"])
    assert hit["source"]["document_id"] == str(doc)
    assert hit["source"]["filename"] == "doc.txt" and hit["score"] > 0


async def test_empty_result_is_ok(search_harness: SearchHarness, db_session: AsyncSession) -> None:
    token, _org, _user = await _login(search_harness, f"s-{uuid4().hex[:8]}")
    await _collection(search_harness, token, "Empty")
    response = await search_harness.client.post(
        "/api/v1/search", headers=_auth(token), json={"query": "no documents here"}
    )
    assert response.status_code == 200
    assert response.json() == {"results": [], "next_cursor": None}


async def test_collection_filter_narrows_results(
    search_harness: SearchHarness, db_session: AsyncSession
) -> None:
    token, org_id, user_id = await _login(search_harness, f"s-{uuid4().hex[:8]}")
    coll_a = await _collection(search_harness, token, "A")
    coll_b = await _collection(search_harness, token, "B")
    await _seed_doc(
        db_session,
        search_harness.embedder,
        org_id=org_id,
        user_id=user_id,
        collection_id=coll_a,
        contents=["shared keyword alpha"],
    )
    await _seed_doc(
        db_session,
        search_harness.embedder,
        org_id=org_id,
        user_id=user_id,
        collection_id=coll_b,
        contents=["shared keyword beta"],
    )
    response = await search_harness.client.post(
        "/api/v1/search",
        headers=_auth(token),
        json={"query": "shared keyword", "collection_id": str(coll_a)},
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert results and all(r["source"]["collection_id"] == str(coll_a) for r in results)


async def test_pagination_caps_and_advances(
    search_harness: SearchHarness, db_session: AsyncSession
) -> None:
    token, org_id, user_id = await _login(search_harness, f"s-{uuid4().hex[:8]}")
    coll = await _collection(search_harness, token, "C")
    await _seed_doc(
        db_session,
        search_harness.embedder,
        org_id=org_id,
        user_id=user_id,
        collection_id=coll,
        contents=[f"common keyword item {i}" for i in range(5)],
    )
    first = (
        await search_harness.client.post(
            "/api/v1/search", headers=_auth(token), json={"query": "common keyword", "limit": 2}
        )
    ).json()
    assert len(first["results"]) == 2 and first["next_cursor"] == 2
    nxt = (
        await search_harness.client.post(
            "/api/v1/search",
            headers=_auth(token),
            json={"query": "common keyword", "limit": 2, "cursor": first["next_cursor"]},
        )
    ).json()
    assert len(nxt["results"]) == 2 and nxt["next_cursor"] == 4
    # the two pages are disjoint
    assert {r["chunk_id"] for r in first["results"]}.isdisjoint(
        {r["chunk_id"] for r in nxt["results"]}
    )


async def test_invalid_filter_is_422(
    search_harness: SearchHarness, db_session: AsyncSession
) -> None:
    token, _org, _user = await _login(search_harness, f"s-{uuid4().hex[:8]}")
    response = await search_harness.client.post(
        "/api/v1/search",
        headers=_auth(token),
        json={"query": "x", "content_type": "image/png"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_SEARCH_FILTER"
