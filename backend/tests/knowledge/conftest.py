"""Fixtures for knowledge API tests: real app with fakes for storage/queue.

The session override flushes and runs post-commit callbacks (so enqueue-after-
commit fires) but never commits — the outer db_session fixture rolls back for
isolation. Upload limit is shrunk so the oversize test needs only a few KB.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.main import create_app
from src.shared.config import Settings, get_settings
from src.shared.database import get_db_session, run_after_commit
from src.shared.queue import get_job_queue
from src.shared.ratelimit import limiter
from src.shared.storage import get_object_storage
from tests.fakes.fake_queue import FakeJobQueue
from tests.fakes.fake_storage import FakeObjectStorage

UPLOAD_LIMIT = 4096


@dataclass
class KnowledgeHarness:
    client: httpx.AsyncClient
    storage: FakeObjectStorage
    queue: FakeJobQueue


@pytest.fixture
async def harness(db_session: AsyncSession, settings: Settings) -> AsyncIterator[KnowledgeHarness]:
    app = create_app()
    storage = FakeObjectStorage()
    queue = FakeJobQueue()
    test_settings = settings.model_copy(update={"max_upload_bytes": UPLOAD_LIMIT})

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.flush()
        await run_after_commit(db_session)

    app.dependency_overrides[get_db_session] = _session_override
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_object_storage] = lambda: storage
    app.dependency_overrides[get_job_queue] = lambda: queue
    limiter.reset()

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield KnowledgeHarness(client=client, storage=storage, queue=queue)


async def register_and_login(client: httpx.AsyncClient, *, slug: str, email: str) -> str:
    """Register an org+owner and return an access token."""
    await client.post(
        "/api/v1/auth/register",
        json={
            "name": slug,
            "slug": slug,
            "owner_email": email,
            "password": "password-123456",
            "full_name": "Owner",
        },
    )
    response = await client.post(
        "/api/v1/auth/login",
        json={"org_slug": slug, "email": email, "password": "password-123456"},
    )
    return str(response.json()["access_token"])
