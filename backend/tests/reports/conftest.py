"""Reports API harness: the real app with the job queue faked (generation runs in the worker,
tested separately) and the rolled-back db_session injected."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.main import create_app
from src.shared.config import Settings, get_settings
from src.shared.database import get_db_session, run_after_commit
from src.shared.queue import get_job_queue
from src.shared.storage import get_object_storage
from tests.fakes.fake_storage import FakeObjectStorage


class FakeQueue:
    """Records enqueues instead of touching Redis."""

    def __init__(self) -> None:
        self.reports: list[tuple[UUID, UUID]] = []

    async def enqueue_ingest(self, *, document_id: UUID, org_id: UUID) -> None:
        return None

    async def enqueue_report(self, *, report_id: UUID, org_id: UUID) -> None:
        self.reports.append((report_id, org_id))

    async def enqueue_export(self, *, export_id: UUID, org_id: UUID) -> None:
        return None

    async def close(self) -> None:
        return None


@dataclass
class ReportHarness:
    client: httpx.AsyncClient
    queue: FakeQueue
    storage: FakeObjectStorage
    db_session: AsyncSession
    settings: Settings = field(default=None)  # type: ignore[assignment]


@pytest.fixture
async def report_harness(
    db_session: AsyncSession, settings: Settings
) -> AsyncIterator[ReportHarness]:
    app = create_app()
    queue = FakeQueue()
    storage = FakeObjectStorage()

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.flush()
        await run_after_commit(db_session)

    app.dependency_overrides[get_db_session] = _session_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_job_queue] = lambda: queue
    app.dependency_overrides[get_object_storage] = lambda: storage

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield ReportHarness(
            client=client, queue=queue, storage=storage, db_session=db_session, settings=settings
        )


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def register_and_login(client: httpx.AsyncClient, slug: str) -> str:
    email = f"o@{slug}.test"
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
    resp = await client.post(
        "/api/v1/auth/login",
        json={"org_slug": slug, "email": email, "password": "password-123456"},
    )
    token: str = resp.json()["access_token"]
    return token
