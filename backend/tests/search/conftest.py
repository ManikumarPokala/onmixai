"""Search API harness: the real app with the embedder faked and the rolled-back
db_session injected (so seeded chunks are visible within the request transaction)."""

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.dependencies import get_embedder
from src.main import create_app
from src.shared.config import Settings, get_settings
from src.shared.database import get_db_session, run_after_commit
from src.shared.ratelimit import limiter
from tests.fakes.fake_embedder import FakeEmbedder


@dataclass
class SearchHarness:
    client: httpx.AsyncClient
    embedder: FakeEmbedder


@pytest.fixture
async def search_harness(
    db_session: AsyncSession, settings: Settings
) -> AsyncIterator[SearchHarness]:
    app = create_app()
    embedder = FakeEmbedder(settings.embedding_dimension)

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.flush()
        await run_after_commit(db_session)

    app.dependency_overrides[get_db_session] = _session_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_embedder] = lambda: embedder
    limiter.reset()

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield SearchHarness(client=client, embedder=embedder)
