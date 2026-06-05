"""Retrieval golden-set v0 — the Phase-2 regression gate (CLAUDE.md §9, §11 #10).

Runs each golden query through the REAL hybrid pipeline (vector arm with the
deterministic fake embedder + keyword/FTS arm + RRF) over a seeded corpus, and
computes recall@5 and MRR. v0 is FTS-anchored: each expected chunk carries a
distinctive token pair, so the gate deterministically validates retrieval
plumbing / ACL / fusion / ranking without a paid model (semantic-quality eval
against a real model is a later, separately-gated addition). Gates recall@5 ≥ 0.85.
"""

import json
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.identity.models import Organization, Role, User
from src.identity.schemas import AuthContext
from src.knowledge.models import Chunk, Collection, CollectionPermission, Document, DocumentStatus
from src.knowledge.repository import ChunkRepository
from src.knowledge.service import ChunkRetrievalService
from src.search.schemas import SearchQuery
from src.search.service import SearchService
from src.shared.audit import AuditEmitter
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.fakes.fake_embedder import FakeEmbedder

_GOLDEN = Path(__file__).parent / "golden_retrieval_v0.jsonl"
_RECALL_FLOOR = 0.85
_K = 5


def _load_golden() -> list[dict[str, str]]:
    return [json.loads(line) for line in _GOLDEN.read_text().splitlines() if line.strip()]


async def _seed_corpus(
    session: AsyncSession, embedder: FakeEmbedder, pairs: list[dict[str, str]]
) -> tuple[AuthContext, dict[str, UUID]]:
    org_id, user_id, collection_id, document_id = uuid4(), uuid4(), uuid4(), uuid4()
    await set_tenant_context(session, org_id)
    session.add(Organization(id=org_id, name="eval", slug=f"eval-{org_id}"))
    session.add(
        User(
            id=user_id,
            org_id=org_id,
            email=f"u@{org_id}.test",
            password_hash="x",
            full_name="U",
            role=Role.OWNER,
        )
    )
    await session.flush()
    session.add(Collection(id=collection_id, org_id=org_id, name="corpus", created_by=user_id))
    await session.flush()
    session.add(
        CollectionPermission(
            org_id=org_id, collection_id=collection_id, user_id=user_id, permission="read"
        )
    )
    session.add(
        Document(
            id=document_id,
            org_id=org_id,
            collection_id=collection_id,
            filename="corpus.txt",
            content_type="text/plain",
            size_bytes=1000,
            storage_key=f"org/{org_id}/doc/{document_id}",
            content_hash=f"{document_id}-h",
            status=DocumentStatus.READY,
            created_by=user_id,
        )
    )
    await session.flush()
    key_to_chunk: dict[str, UUID] = {}
    for i, pair in enumerate(pairs):
        chunk_id = uuid4()
        key_to_chunk[pair["key"]] = chunk_id
        session.add(
            Chunk(
                id=chunk_id,
                org_id=org_id,
                document_id=document_id,
                seq=i,
                content=pair["content"],
                content_hash=f"{chunk_id}-h",
                token_count=len(pair["content"].split()),
                chunk_metadata={"key": pair["key"]},
                embedding=embedder._vector(pair["content"]),
            )
        )
    await session.flush()
    return AuthContext(user_id=user_id, org_id=org_id, role=Role.OWNER), key_to_chunk


async def test_golden_retrieval_recall_at_5(db_session: AsyncSession, settings: Settings) -> None:
    pairs = _load_golden()
    assert len(pairs) >= 50  # golden sets only grow
    embedder = FakeEmbedder(settings.embedding_dimension)
    actor, key_to_chunk = await _seed_corpus(db_session, embedder, pairs)
    service = SearchService(
        reader=ChunkRetrievalService(ChunkRepository(db_session), settings),
        embedder=embedder,
        audit=AuditEmitter(),
        settings=settings,
    )

    hits = 0
    reciprocal_rank = 0.0
    for pair in pairs:
        result = await service.search(actor, SearchQuery(query=pair["query"], limit=_K))
        ranked = [item.chunk_id for item in result.results]
        expected = key_to_chunk[pair["key"]]
        if expected in ranked:
            hits += 1
            reciprocal_rank += 1.0 / (ranked.index(expected) + 1)

    total = len(pairs)
    recall_at_5 = hits / total
    mrr = reciprocal_rank / total
    print(f"\n[golden v0] recall@{_K}={recall_at_5:.3f} MRR={mrr:.3f} over {total} queries")
    assert recall_at_5 >= _RECALL_FLOOR, f"recall@{_K}={recall_at_5:.3f} < {_RECALL_FLOOR}"
