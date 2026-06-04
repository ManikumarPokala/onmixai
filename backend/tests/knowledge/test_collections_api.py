"""API tests for collections + permissions."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.models import Role, User
from src.identity.repository import UserRepository
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from src.shared.security import create_access_token
from tests.knowledge.conftest import KnowledgeHarness, register_and_login


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_create_and_list_collection(harness: KnowledgeHarness) -> None:
    token = await register_and_login(harness.client, slug="acme", email="o@acme.test")
    created = await harness.client.post(
        "/api/v1/collections", json={"name": "Docs", "description": "d"}, headers=_auth(token)
    )
    assert created.status_code == 201
    collection_id = created.json()["id"]

    listed = await harness.client.get("/api/v1/collections", headers=_auth(token))
    assert listed.status_code == 200
    assert [c["id"] for c in listed.json()] == [collection_id]


async def test_duplicate_collection_name_conflicts(harness: KnowledgeHarness) -> None:
    token = await register_and_login(harness.client, slug="acme", email="o@acme.test")
    await harness.client.post("/api/v1/collections", json={"name": "Docs"}, headers=_auth(token))
    dup = await harness.client.post(
        "/api/v1/collections", json={"name": "Docs"}, headers=_auth(token)
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "COLLECTION_NAME_TAKEN"


async def test_non_permitted_user_cannot_see_or_use_others_collection(
    harness: KnowledgeHarness, db_session: AsyncSession, settings: Settings
) -> None:
    owner_token = await register_and_login(harness.client, slug="acme", email="o@acme.test")
    created = await harness.client.post(
        "/api/v1/collections", json={"name": "Docs"}, headers=_auth(owner_token)
    )
    collection_id = created.json()["id"]
    org_id = UUID(
        (await harness.client.get("/api/v1/users/me", headers=_auth(owner_token))).json()["org_id"]
    )

    # A second user in the same org with no permission on the collection.
    await set_tenant_context(db_session, org_id)
    other = await UserRepository(db_session).create(
        User(org_id=org_id, email="b@acme.test", password_hash="x", full_name="B", role=Role.MEMBER)
    )
    other_token = create_access_token(
        settings=settings, user_id=other.id, org_id=org_id, role="member"
    )

    assert (
        await harness.client.get("/api/v1/collections", headers=_auth(other_token))
    ).json() == []

    upload = await harness.client.post(
        f"/api/v1/collections/{collection_id}/documents",
        files={"file": ("f.txt", b"hello", "text/plain")},
        headers=_auth(other_token),
    )
    assert upload.status_code == 403
    assert upload.json()["error"]["code"] == "COLLECTION_ACCESS_DENIED"
