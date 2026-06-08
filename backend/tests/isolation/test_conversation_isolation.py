"""Isolation suite — the conversation surface (blocking forever after).

Two axes, both run as the non-superuser/non-bypassrls runtime role so application scoping
AND Postgres RLS are exercised:

  * tenant (org) — org A's actor can never reach org B's sessions/messages/feedback/summaries.
  * user (NEW in Phase 4) — within ONE org, user A1 can never list, read, message, delete, or
    leave feedback on user A2's private sessions/messages (indistinguishable from missing —
    a 404, no existence oracle).

Plus a raw-unfiltered-count RLS proof on all four conversation tables, and a re-proof of the
Phase-2 retrieval ACL through the chat surface: the grounded pipeline can never retrieve or
cite a chunk the requester has no permission to read.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.prompt_registry import get_prompt_registry
from src.conversation.exceptions import MessageNotFoundError, SessionNotFoundError
from src.conversation.models import ChatMessage, ChatRole, ChatSession, MessageFeedback
from src.conversation.pipeline import GroundedAnswerPipeline
from src.conversation.repository import (
    ChatMessageRepository,
    ChatSessionRepository,
    MessageFeedbackRepository,
    SessionSummaryRepository,
)
from src.conversation.schemas import CitationsEvent, FeedbackRequest, RefusalEvent
from src.conversation.service import ChatService
from src.identity.models import Organization, Role, User
from src.identity.schemas import AuthContext
from src.knowledge.models import Chunk, Collection, CollectionPermission, Document, DocumentStatus
from src.knowledge.repository import ChunkRepository
from src.knowledge.service import ChunkRetrievalService
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
    session_id: UUID
    message_id: UUID


def _service(session: AsyncSession, settings: Settings, gateway: FakeGateway) -> ChatService:
    embedder = FakeEmbedder(settings.embedding_dimension)
    retriever = SearchService(
        reader=ChunkRetrievalService(ChunkRepository(session), settings),
        embedder=embedder,
        audit=AuditEmitter(),
        settings=settings,
    )
    pipeline = GroundedAnswerPipeline(
        retriever=retriever, gateway=gateway, registry=get_prompt_registry(), settings=settings
    )
    return ChatService(
        sessions=ChatSessionRepository(session),
        messages=ChatMessageRepository(session),
        feedback=MessageFeedbackRepository(session),
        summaries=SessionSummaryRepository(session),
        pipeline=pipeline,
        audit=AuditEmitter(),
        settings=settings,
    )


async def _seed_user(session: AsyncSession, org_id: UUID, label: str) -> _User:
    """A user in ``org_id`` with one private session that holds one assistant message."""
    user_id, session_id, message_id = uuid4(), uuid4(), uuid4()
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
        ChatSession(id=session_id, org_id=org_id, owner_user_id=user_id, title=f"{label} chat")
    )
    await session.flush()
    session.add(
        ChatMessage(
            id=message_id,
            org_id=org_id,
            session_id=session_id,
            role=ChatRole.ASSISTANT,
            content="An answer [1].",
            seq=1,
        )
    )
    await session.flush()
    actor = AuthContext(user_id=user_id, org_id=org_id, role=Role.MEMBER)
    return _User(org_id, user_id, actor, session_id, message_id)


@pytest.fixture
async def same_org(db_session: AsyncSession) -> AsyncIterator[tuple[_User, _User]]:
    """Two users (A1, A2) in the SAME org."""
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


async def test_user_cannot_read_anothers_session_messages(
    same_org: tuple[_User, _User], db_session: AsyncSession, settings: Settings
) -> None:
    a1, a2 = same_org
    await set_tenant_context(db_session, a1.org_id)
    service = _service(db_session, settings, FakeGateway())
    with pytest.raises(SessionNotFoundError):  # A2's session is invisible to A1 (no oracle)
        await service.list_messages(a1.actor, a2.session_id, after_seq=None, limit=50)
    # A1 can read its own.
    own = await service.list_messages(a1.actor, a1.session_id, after_seq=None, limit=50)
    assert len(own.messages) == 1


async def test_user_session_list_excludes_other_users_sessions(
    same_org: tuple[_User, _User], db_session: AsyncSession, settings: Settings
) -> None:
    a1, a2 = same_org
    await set_tenant_context(db_session, a1.org_id)
    service = _service(db_session, settings, FakeGateway())
    page = await service.list_sessions(a1.actor, cursor=None, limit=50)
    ids = {s.id for s in page.sessions}
    assert a1.session_id in ids and a2.session_id not in ids


async def test_user_cannot_update_or_delete_anothers_session(
    same_org: tuple[_User, _User], db_session: AsyncSession, settings: Settings
) -> None:
    a1, a2 = same_org
    await set_tenant_context(db_session, a1.org_id)
    service = _service(db_session, settings, FakeGateway())
    with pytest.raises(SessionNotFoundError):
        await service.update_session(a1.actor, a2.session_id, title="hijack", is_archived=None)
    with pytest.raises(SessionNotFoundError):
        await service.delete_session(a1.actor, a2.session_id)
    # A2's session still exists and is untouched.
    await set_tenant_context(db_session, a2.org_id)
    assert await ChatSessionRepository(db_session).get(a2.org_id, a2.session_id) is not None


async def test_user_cannot_feedback_anothers_message(
    same_org: tuple[_User, _User], db_session: AsyncSession, settings: Settings
) -> None:
    a1, a2 = same_org
    await set_tenant_context(db_session, a1.org_id)
    service = _service(db_session, settings, FakeGateway())
    with pytest.raises((SessionNotFoundError, MessageNotFoundError)):
        await service.submit_feedback(a1.actor, a2.message_id, FeedbackRequest(rating="up"))


# --- tenant (org) axis ---


async def test_cross_org_session_and_message_are_invisible(
    cross_org: tuple[_User, _User], db_session: AsyncSession, settings: Settings
) -> None:
    a, b = cross_org
    await set_tenant_context(db_session, a.org_id)
    service = _service(db_session, settings, FakeGateway())
    with pytest.raises(SessionNotFoundError):
        await service.list_messages(a.actor, b.session_id, after_seq=None, limit=50)
    with pytest.raises(SessionNotFoundError):
        await service.delete_session(a.actor, b.session_id)
    with pytest.raises((SessionNotFoundError, MessageNotFoundError)):
        await service.submit_feedback(a.actor, b.message_id, FeedbackRequest(rating="down"))


# --- raw-count RLS proof on all four conversation tables ---


async def test_raw_counts_respect_rls_on_all_conversation_tables(
    cross_org: tuple[_User, _User], db_session: AsyncSession
) -> None:
    a, b = cross_org
    # Give each side a feedback row + a summary row so all four tables are populated. Each
    # write flushes under ITS OWN tenant context (a deferred flush would run under the last
    # context and trip RLS — which is itself the guarantee under test).
    for actor in (a, b):
        await set_tenant_context(db_session, actor.org_id)
        db_session.add(
            MessageFeedback(
                org_id=actor.org_id,
                message_id=actor.message_id,
                user_id=actor.user_id,
                rating="up",
            )
        )
        await db_session.flush()
        await db_session.execute(
            text(
                "INSERT INTO session_summaries (id, org_id, session_id, summary, through_seq) "
                "VALUES (gen_random_uuid(), :org, :sess, 'sum', 1)"
            ),
            {"org": str(actor.org_id), "sess": str(actor.session_id)},
        )

    tables = ["chat_sessions", "chat_messages", "message_feedback", "session_summaries"]
    for actor in (a, b):
        await set_tenant_context(db_session, actor.org_id)
        for table in tables:
            count = (await db_session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
            assert count == 1, f"{table} leaked across org for {actor.org_id}"  # RLS, no WHERE


# --- citation-hydration ACL (Phase-2 guarantee re-proven through chat) ---


async def _seed_private_chunk(
    session: AsyncSession, org_id: UUID, owner_user_id: UUID, term: str, dim: int
) -> None:
    """A chunk in a collection only ``owner_user_id`` can read (no permission for anyone else)."""
    collection_id, document_id = uuid4(), uuid4()
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
            id=uuid4(),
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


async def test_chat_cannot_retrieve_or_cite_a_chunk_outside_the_requesters_acl(
    same_org: tuple[_User, _User], db_session: AsyncSession, settings: Settings
) -> None:
    a1, a2 = same_org
    term = "quixotrope"
    await _seed_private_chunk(
        db_session, a1.org_id, a2.user_id, term, settings.embedding_dimension
    )  # only A2 may read it

    gateway = FakeGateway()
    gateway.queue_stream(["The ", f"{term} [1]."])  # would cite IF a source were retrieved

    # A1 (no permission) asks about A2's private term → retrieval returns zero → refusal, and
    # no citation to A2's chunk is ever produced.
    await set_tenant_context(db_session, a1.org_id)
    service = _service(db_session, settings, gateway)
    events = [
        e
        async for e in service.send_message_stream(
            a1.actor, a1.session_id, f"What is the {term}?", request_id="iso"
        )
    ]
    assert any(isinstance(e, RefusalEvent) for e in events)
    assert not any(isinstance(e, CitationsEvent) for e in events)

    # Positive control: A2 (who has access) retrieves and cites the same chunk.
    gateway2 = FakeGateway()
    gateway2.queue_stream(["The ", f"{term} [1]."])
    await set_tenant_context(db_session, a2.org_id)
    service2 = _service(db_session, settings, gateway2)
    events2 = [
        e
        async for e in service2.send_message_stream(
            a2.actor, a2.session_id, f"What is the {term}?", request_id="iso"
        )
    ]
    citations = [e for e in events2 if isinstance(e, CitationsEvent)]
    assert citations and citations[0].items  # A2 gets a real, ACL-permitted citation
    assert not any(isinstance(e, RefusalEvent) for e in events2)
