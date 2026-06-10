"""The demo path, end-to-end and rot-proof: over the seeded demo corpus, the answerable query
returns a cited answer pointing at the SOP, and the refusal query — a safety parameter for a
material genuinely NOT in the corpus — is refused rather than fabricated. The exact queries live in
scripts/demo_corpus.py (shared with the seed script) so the demo you show and the demo CI checks
can't drift. Generation is the deterministic FakeGateway; retrieval/grounding are the real path."""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.demo_corpus import (
    ANSWERABLE_DOC,
    ANSWERABLE_FACT,
    ANSWERABLE_QUERY,
    DOCS,
    REFUSAL_ABSENT_TERM,
    REFUSAL_QUERY,
)
from src.ai.guardrails import Refusal
from src.ai.prompt_registry import get_prompt_registry
from src.conversation.pipeline import AnsweredTurn, GroundedAnswerPipeline
from src.identity.models import Organization, Role, User
from src.identity.schemas import AuthContext
from src.knowledge.models import (
    Chunk,
    Collection,
    CollectionPermission,
    Document,
    DocumentStatus,
)
from src.knowledge.repository import ChunkRepository
from src.knowledge.service import ChunkRetrievalService
from src.search.service import SearchService
from src.shared.audit import AuditEmitter
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.fakes.fake_embedder import FakeEmbedder
from tests.fakes.fake_gateway import FakeGateway


async def _seed_demo(
    session: AsyncSession, embedder: FakeEmbedder
) -> tuple[AuthContext, dict[UUID, str]]:
    """Seed the demo corpus as READY documents + one embedded chunk each (the eval pattern — the
    full ingestion pipeline has its own tests; here we exercise the chat path). Returns the actor
    and a chunk_id → filename map."""
    org_id, user_id, collection_id = uuid4(), uuid4(), uuid4()
    await set_tenant_context(session, org_id)
    session.add(Organization(id=org_id, name="Demo", slug=f"demo-{org_id}"))
    await session.flush()
    session.add(
        User(
            id=user_id,
            org_id=org_id,
            email=f"op-{user_id}@demo.test",
            password_hash="x",
            full_name="Operator",
            role=Role.OWNER,
        )
    )
    session.add(Collection(id=collection_id, org_id=org_id, name="ops", created_by=user_id))
    await session.flush()
    session.add(
        CollectionPermission(
            org_id=org_id, collection_id=collection_id, user_id=user_id, permission="read"
        )
    )
    chunk_to_file: dict[UUID, str] = {}
    for doc in DOCS:
        document_id, chunk_id = uuid4(), uuid4()
        session.add(
            Document(
                id=document_id,
                org_id=org_id,
                collection_id=collection_id,
                filename=doc.filename,
                content_type="text/plain",
                size_bytes=len(doc.content),
                storage_key=f"org/{org_id}/doc/{document_id}",
                content_hash=f"{document_id}-h",
                status=DocumentStatus.READY,
                created_by=user_id,
            )
        )
        session.add(
            Chunk(
                id=chunk_id,
                org_id=org_id,
                document_id=document_id,
                seq=0,
                content=doc.content,
                content_hash=f"{chunk_id}-h",
                token_count=len(doc.content.split()),
                chunk_metadata={"filename": doc.filename},
                embedding=embedder._vector(doc.content),
            )
        )
        chunk_to_file[chunk_id] = doc.filename
    await session.flush()
    return AuthContext(user_id=user_id, org_id=org_id, role=Role.OWNER), chunk_to_file


def _pipeline(
    session: AsyncSession, settings: Settings, gateway: FakeGateway
) -> GroundedAnswerPipeline:
    embedder = FakeEmbedder(settings.embedding_dimension)
    retriever = SearchService(
        reader=ChunkRetrievalService(ChunkRepository(session), settings),
        embedder=embedder,
        audit=AuditEmitter(),
        settings=settings,
    )
    return GroundedAnswerPipeline(
        retriever=retriever, gateway=gateway, registry=get_prompt_registry(), settings=settings
    )


async def test_demo_corpus_is_ready_with_embeddings(
    db_session: AsyncSession, settings: Settings
) -> None:
    await _seed_demo(db_session, FakeEmbedder(settings.embedding_dimension))
    ready = (
        (await db_session.execute(select(Document).where(Document.status == DocumentStatus.READY)))
        .scalars()
        .all()
    )
    assert len(ready) == len(DOCS)  # every demo doc reached READY
    embedded = (
        (await db_session.execute(select(Chunk).where(Chunk.embedding.is_not(None))))
        .scalars()
        .all()
    )
    assert len(embedded) == len(DOCS)  # each has an embedded chunk


async def test_refusal_term_is_genuinely_out_of_corpus() -> None:
    # The honest precondition for the refusal story: the queried material is in NO document.
    assert all(REFUSAL_ABSENT_TERM.lower() not in d.content.lower() for d in DOCS)


async def test_answerable_query_returns_a_cited_answer_to_the_sop(
    db_session: AsyncSession, settings: Settings
) -> None:
    embedder = FakeEmbedder(settings.embedding_dimension)
    actor, chunk_to_file = await _seed_demo(db_session, embedder)
    gateway = FakeGateway()
    gateway.queue_completion(
        text=(
            f"The Reactor R-200 jacket is preheated to {ANSWERABLE_FACT} degrees C "
            "during startup [1]."
        )
    )
    outcome = await _pipeline(db_session, settings, gateway).answer(
        actor=actor, raw_query=ANSWERABLE_QUERY, history=[], summary=None, request_id="demo"
    )
    assert isinstance(outcome, AnsweredTurn)
    assert ANSWERABLE_FACT in outcome.content
    cited_files = {chunk_to_file[c.chunk_id] for c in outcome.citations}
    assert ANSWERABLE_DOC in cited_files  # the citation resolves to the SOP, precisely


async def test_refusal_query_refuses_rather_than_fabricates(
    db_session: AsyncSession, settings: Settings
) -> None:
    embedder = FakeEmbedder(settings.embedding_dimension)
    actor, _ = await _seed_demo(db_session, embedder)
    gateway = FakeGateway()
    # The model has no source for hydrazine, so it answers without a citation — which the pipeline
    # refuses as ungrounded rather than letting a guessed safety value through.
    gateway.queue_completion(
        text=(
            "I don't have a documented source for hydrazine's exposure limit, "
            "so I can't provide it."
        )
    )
    outcome = await _pipeline(db_session, settings, gateway).answer(
        actor=actor, raw_query=REFUSAL_QUERY, history=[], summary=None, request_id="demo"
    )
    assert isinstance(outcome, Refusal)  # refused, NOT a fabricated answer
