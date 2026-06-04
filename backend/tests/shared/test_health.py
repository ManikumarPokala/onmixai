"""Tests for the liveness and readiness probes."""

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.main import create_app
from src.shared.health import readiness_engine


def _client(engine: AsyncEngine) -> httpx.AsyncClient:
    app = create_app()
    app.dependency_overrides[readiness_engine] = lambda: engine
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
async def down_engine() -> AsyncIterator[AsyncEngine]:
    # Points at a refused port so the readiness probe fails fast.
    engine = create_async_engine("postgresql+asyncpg://x:x@127.0.0.1:1/none")
    yield engine
    await engine.dispose()


async def test_liveness_ok_even_when_db_down(down_engine: AsyncEngine) -> None:
    async with _client(down_engine) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readiness_degraded_when_db_down(down_engine: AsyncEngine) -> None:
    async with _client(down_engine) as client:
        response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "checks": {"database": "down"}}


async def test_readiness_ok_when_db_up(app_engine: AsyncEngine) -> None:
    async with _client(app_engine) as client:
        response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"]["database"] == "up"
