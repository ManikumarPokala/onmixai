"""Admin API harness: the real app with the rolled-back db_session injected and real JWT auth
(so require_admin runs for real). Seeds an org with one user per role + their access tokens."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.models import Organization, Role, User
from src.main import create_app
from src.shared.config import Settings, get_settings
from src.shared.database import get_db_session, run_after_commit, set_tenant_context
from src.shared.queue import get_job_queue
from src.shared.ratelimit import limiter
from src.shared.security import create_access_token
from src.shared.storage import get_object_storage
from tests.fakes.fake_queue import FakeJobQueue
from tests.fakes.fake_storage import FakeObjectStorage


@dataclass
class AdminOrg:
    org_id: UUID
    tokens: dict[Role, str]
    user_ids: dict[Role, UUID]


@dataclass
class AdminHarness:
    client: httpx.AsyncClient
    db_session: AsyncSession
    settings: Settings
    storage: FakeObjectStorage
    queue: FakeJobQueue


@pytest.fixture
async def admin_harness(
    db_session: AsyncSession, settings: Settings
) -> AsyncIterator[AdminHarness]:
    app = create_app()
    storage = FakeObjectStorage()
    queue = FakeJobQueue()

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.flush()
        await run_after_commit(db_session)

    app.dependency_overrides[get_db_session] = _session_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_object_storage] = lambda: storage
    app.dependency_overrides[get_job_queue] = lambda: queue
    limiter.reset()

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield AdminHarness(
            client=client, db_session=db_session, settings=settings, storage=storage, queue=queue
        )


async def seed_org(session: AsyncSession, settings: Settings, slug: str = "acme") -> AdminOrg:
    """An org with one OWNER, ADMIN, and MEMBER, each with a minted access token."""
    org_id = uuid4()
    await set_tenant_context(session, org_id)
    session.add(Organization(id=org_id, name=slug, slug=f"{slug}-{org_id}"))
    await session.flush()
    tokens: dict[Role, str] = {}
    user_ids: dict[Role, UUID] = {}
    for role in (Role.OWNER, Role.ADMIN, Role.MEMBER):
        uid = uuid4()
        session.add(
            User(
                id=uid,
                org_id=org_id,
                email=f"{role.value}-{uid}@{slug}.test",
                password_hash="x",
                full_name=role.value,
                role=role,
            )
        )
        user_ids[role] = uid
        tokens[role] = create_access_token(
            settings=settings, user_id=uid, org_id=org_id, role=role.value
        )
    await session.flush()
    return AdminOrg(org_id=org_id, tokens=tokens, user_ids=user_ids)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
