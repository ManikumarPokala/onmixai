"""Recommendation API harness: the real app with the LLM gateway and the retriever faked,
and the rolled-back db_session injected — so completed/declined outcomes are deterministic
and isolated from search internals."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.dependencies import get_gateway
from src.identity.schemas import AuthContext
from src.main import create_app
from src.search.dependencies import get_search_service
from src.search.schemas import SearchQuery, SearchResult, SearchResultItem, SourceAttribution
from src.shared.config import Settings, get_settings
from src.shared.database import get_db_session, run_after_commit
from src.shared.ratelimit import limiter
from tests.fakes.fake_gateway import FakeGateway


class FakeRetriever:
    """A settable stand-in for search.SearchService (the Retriever port)."""

    def __init__(self) -> None:
        self.results: list[SearchResultItem] = []

    def set_sources(self, *scores: float) -> None:
        self.results = [
            SearchResultItem(
                chunk_id=uuid4(),
                content=f"source text {i}",
                score=score,
                source=SourceAttribution(
                    document_id=uuid4(), collection_id=uuid4(), filename="doc.txt", ref={"page": 3}
                ),
            )
            for i, score in enumerate(scores)
        ]

    async def search(self, actor: AuthContext, query: SearchQuery) -> SearchResult:
        return SearchResult(results=self.results, next_cursor=None)


@dataclass
class RecHarness:
    client: httpx.AsyncClient
    gateway: FakeGateway
    retriever: FakeRetriever
    db_session: AsyncSession
    settings: Settings = field(default=None)  # type: ignore[assignment]


@pytest.fixture
async def rec_harness(db_session: AsyncSession, settings: Settings) -> AsyncIterator[RecHarness]:
    app = create_app()
    gateway = FakeGateway()
    retriever = FakeRetriever()

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.flush()
        await run_after_commit(db_session)

    app.dependency_overrides[get_db_session] = _session_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_gateway] = lambda: gateway
    app.dependency_overrides[get_search_service] = lambda: retriever
    limiter.reset()

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield RecHarness(
            client=client,
            gateway=gateway,
            retriever=retriever,
            db_session=db_session,
            settings=settings,
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
