"""Isolation suite — the recommendation surface (blocking forever after).

Two axes, both run as the non-superuser/non-bypassrls runtime role so application scoping AND
Postgres RLS are exercised:

  * tenant (org) — org A's actor can never reach org B's recommendations.
  * user — within ONE org, user A1 can never read or list user A2's recommendations
    (indistinguishable from missing — a 404, no existence oracle).

Plus a raw-unfiltered-count RLS proof on the recommendations table, and a re-proof of the
Phase-2 retrieval ACL through the recommendation surface: a recommendation can never retrieve
or cite a chunk the requester has no permission to read (it declines instead).
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.prompt_registry import get_prompt_registry
from src.identity.models import Organization, Role, User
from src.identity.schemas import AuthContext
from src.knowledge.models import Chunk, Collection, CollectionPermission, Document, DocumentStatus
from src.knowledge.repository import ChunkRepository
from src.knowledge.service import ChunkRetrievalService
from src.recommendation.exceptions import RecommendationNotFoundError
from src.recommendation.models import ConfidenceBand, Recommendation, RecommendationStatus
from src.recommendation.pipeline import RecommendationPipeline
from src.recommendation.repository import RecommendationRepository
from src.recommendation.service import RecommendationService
from src.search.schemas import SearchQuery
from src.search.service import SearchService
from src.shared.audit import AuditEmitter
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.fakes.fake_embedder import FakeEmbedder
from tests.fakes.fake_gateway import FakeGateway


@dataclass(frozen=True)
class _User:
    org_id: UUID
    user_id: UUID
    actor: AuthContext
    recommendation_id: UUID


def _retriever(session: AsyncSession, settings: Settings) -> SearchService:
    return SearchService(
        reader=ChunkRetrievalService(ChunkRepository(session), settings),
        embedder=FakeEmbedder(settings.embedding_dimension),
        audit=AuditEmitter(),
        settings=settings,
    )


def _service(
    session: AsyncSession, settings: Settings, gateway: FakeGateway
) -> RecommendationService:
    pipeline = RecommendationPipeline(
        retriever=_retriever(session, settings),
        gateway=gateway,
        registry=get_prompt_registry(),
        settings=settings,
    )
    return RecommendationService(
        repository=RecommendationRepository(session),
        pipeline=pipeline,
        audit=AuditEmitter(),
        settings=settings,
    )


async def _seed_user(session: AsyncSession, org_id: UUID, label: str) -> _User:
    """A user in ``org_id`` who owns one completed recommendation."""
    user_id, rec_id = uuid4(), uuid4()
    session.add(
        User(
            id=user_id,
            org_id=org_id,
            email=f"{label}-{user_id}@x.test",
            password_hash="x",
            full_name=label,
            role=Role.MEMBER,
        )
    )
    await session.flush()
    session.add(
        Recommendation(
            id=rec_id,
            org_id=org_id,
            created_by=user_id,
            query=f"{label} decision",
            status=RecommendationStatus.COMPLETED,
            confidence_band=ConfidenceBand.HIGH,
            payload={"output": {"recommendation": "x"}, "citations": []},
            prompt_version="1.0.0",
        )
    )
    await session.flush()
    actor = AuthContext(user_id=user_id, org_id=org_id, role=Role.MEMBER)
    return _User(org_id, user_id, actor, rec_id)


@pytest.fixture
async def same_org(db_session: AsyncSession) -> AsyncIterator[tuple[_User, _User]]:
    """Two users (A1, A2) in the SAME org, each owning a recommendation."""
    org_id = uuid4()
    await set_tenant_context(db_session, org_id)
    db_session.add(Organization(id=org_id, name="OrgA", slug=f"org-a-{org_id}"))
    await db_session.flush()
    a1 = await _seed_user(db_session, org_id, "a1")
    a2 = await _seed_user(db_session, org_id, "a2")
    yield a1, a2


@pytest.fixture
async def cross_org(db_session: AsyncSession) -> AsyncIterator[tuple[_User, _User]]:
    """One user per org (A in org A, B in org B)."""
    org_a, org_b = uuid4(), uuid4()
    for org, label in ((org_a, "OrgA"), (org_b, "OrgB")):
        await set_tenant_context(db_session, org)
        db_session.add(Organization(id=org, name=label, slug=f"{label}-{org}"))
        await db_session.flush()
    await set_tenant_context(db_session, org_a)
    a = await _seed_user(db_session, org_a, "a")
    await set_tenant_context(db_session, org_b)
    b = await _seed_user(db_session, org_b, "b")
    yield a, b


# --- user-level axis (same org) ---


async def test_user_cannot_read_anothers_recommendation(
    same_org: tuple[_User, _User], db_session: AsyncSession, settings: Settings
) -> None:
    a1, a2 = same_org
    await set_tenant_context(db_session, a1.org_id)
    service = _service(db_session, settings, FakeGateway())
    with pytest.raises(RecommendationNotFoundError):  # A2's rec is invisible to A1 (no oracle)
        await service.get(a1.actor, a2.recommendation_id)
    own = await service.get(a1.actor, a1.recommendation_id)  # A1 reads its own
    assert own.id == a1.recommendation_id


async def test_user_list_excludes_other_users_recommendations(
    same_org: tuple[_User, _User], db_session: AsyncSession, settings: Settings
) -> None:
    a1, a2 = same_org
    await set_tenant_context(db_session, a1.org_id)
    service = _service(db_session, settings, FakeGateway())
    page = await service.list(a1.actor, cursor=None, limit=50)
    ids = {r.id for r in page.recommendations}
    assert a1.recommendation_id in ids and a2.recommendation_id not in ids


# --- tenant (org) axis ---


async def test_cross_org_recommendation_is_invisible(
    cross_org: tuple[_User, _User], db_session: AsyncSession, settings: Settings
) -> None:
    a, b = cross_org
    await set_tenant_context(db_session, a.org_id)
    service = _service(db_session, settings, FakeGateway())
    with pytest.raises(RecommendationNotFoundError):
        await service.get(a.actor, b.recommendation_id)
    page = await service.list(a.actor, cursor=None, limit=50)
    assert b.recommendation_id not in {r.id for r in page.recommendations}


# --- raw-count RLS proof on the recommendations table ---


async def test_raw_counts_respect_rls_on_recommendations(
    cross_org: tuple[_User, _User], db_session: AsyncSession
) -> None:
    a, b = cross_org
    for actor in (a, b):
        await set_tenant_context(db_session, actor.org_id)
        count = (
            await db_session.execute(text("SELECT count(*) FROM recommendations"))
        ).scalar_one()
        assert count == 1, f"recommendations leaked across org for {actor.org_id}"  # RLS, no WHERE


# --- citation-hydration ACL (Phase-2 guarantee re-proven through recommendation) ---


async def _seed_private_chunk(
    session: AsyncSession, org_id: UUID, owner_user_id: UUID, term: str, dim: int
) -> UUID:
    """A chunk in a collection only ``owner_user_id`` can read (no permission for anyone else)."""
    collection_id, document_id, chunk_id = uuid4(), uuid4(), uuid4()
    await set_tenant_context(session, org_id)
    session.add(
        Collection(id=collection_id, org_id=org_id, name="private", created_by=owner_user_id)
    )
    await session.flush()
    session.add(
        CollectionPermission(
            org_id=org_id, collection_id=collection_id, user_id=owner_user_id, permission="read"
        )
    )
    session.add(
        Document(
            id=document_id,
            org_id=org_id,
            collection_id=collection_id,
            filename="private.txt",
            content_type="text/plain",
            size_bytes=50,
            storage_key=f"org/{org_id}/doc/{document_id}",
            content_hash=f"{document_id}-h",
            status=DocumentStatus.READY,
            created_by=owner_user_id,
        )
    )
    await session.flush()
    embedder = FakeEmbedder(dim)
    content = f"The {term} is a private secret recorded only here."
    session.add(
        Chunk(
            id=chunk_id,
            org_id=org_id,
            document_id=document_id,
            seq=0,
            content=content,
            content_hash=f"{uuid4()}-h",
            token_count=len(content.split()),
            chunk_metadata={},
            embedding=embedder._vector(content),
        )
    )
    await session.flush()
    return chunk_id


async def test_recommendation_cannot_retrieve_or_cite_a_chunk_outside_the_requesters_acl(
    same_org: tuple[_User, _User], db_session: AsyncSession, settings: Settings
) -> None:
    a1, a2 = same_org
    term = "quixotrope"
    private_chunk = await _seed_private_chunk(
        db_session, a1.org_id, a2.user_id, term, settings.embedding_dimension
    )  # only A2 may read it

    # A1 (no permission) asks for a recommendation grounded in A2's private term. Retrieval
    # returns zero → the recommendation DECLINES; no citation to A2's chunk is ever produced.
    gateway = FakeGateway()  # would have produced a rec IF any source were retrieved
    await set_tenant_context(db_session, a1.org_id)
    service = _service(db_session, settings, gateway)
    result = await service.create(
        a1.actor,
        query=f"What should we do about the {term}?",
        collection_scope=[],
        request_id="iso",
    )
    assert result.status == "declined"
    assert result.confidence_band is None
    assert gateway.calls == []  # declined BEFORE generation — never reached the model
    assert all(c.chunk_id != private_chunk for c in result.citations)

    # Positive control through the recommendation's own retriever: A2 (who has access) DOES
    # retrieve the same chunk — proving the difference is purely ACL, not absence of the chunk.
    await set_tenant_context(db_session, a2.org_id)
    a2_candidates = await _retriever(db_session, settings).search(
        a2.actor, SearchQuery(query=term, limit=20)
    )
    assert any(item.chunk_id == private_chunk for item in a2_candidates.results)
