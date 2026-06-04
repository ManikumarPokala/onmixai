"""Fixtures for identity API tests.

Builds the real application via create_app() but overrides the DB session with
the testcontainer-backed, rolled-back session and the settings with the test
settings, so requests share one transaction (no data leaks) and tokens are
signed/verified with the same secret. The shared rate limiter is reset per test.
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.main import create_app
from src.shared.config import Settings, get_settings
from src.shared.database import get_db_session
from src.shared.ratelimit import limiter


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    limiter.reset()


@pytest.fixture
async def api_client(
    db_session: AsyncSession, settings: Settings
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = _session_override
    app.dependency_overrides[get_settings] = lambda: settings

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
