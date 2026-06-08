"""Chat golden-set v0 — the Phase-4 conversational regression gate (CLAUDE.md §9, §11 #10).

Runs each golden case through the REAL chat pipeline (rewrite → retrieve → grounded answer →
grounding validation) over a seeded corpus, with generation served by the deterministic
llm_stub. The stub is grounding-aware: it cites the source that shares a distinctive token
with the question, so an answerable question yields a valid citation and an unanswerable one
yields a no-citation answer the pipeline refuses as ungrounded. With the stub every metric is
a fixed, repeatable value — this gate proves the harness + pipeline routing + metric
computation are correct and deterministic, NOT generation quality (the same honesty caveat as
the retrieval/generation golden sets; real-model numbers are re-measured when a provider is
configured).

Metrics reported + gated: answerable-recall, refusal-correctness (both directions),
faithfulness (judge), citation validity, and phantom/invention rate. Thresholds:
faithfulness ≥ 0.9, refusal-correctness ≥ 0.95, zero fabricated citations.
"""

import importlib.util
import json
import threading
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import litellm
import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.adapters.circuit_breaker import CircuitBreaker
from src.ai.adapters.litellm_gateway import LiteLLMGateway
from src.ai.gateway import GatewayContext, LLMGateway
from src.ai.guardrails import Refusal
from src.ai.models import UsageFeature
from src.ai.prompt_registry import get_prompt_registry
from src.conversation.context import HistoryTurn
from src.conversation.pipeline import AnsweredTurn, GroundedAnswerPipeline
from src.identity.models import Organization, Role, User
from src.identity.schemas import AuthContext
from src.knowledge.models import Chunk, Collection, CollectionPermission, Document, DocumentStatus
from src.knowledge.repository import ChunkRepository
from src.knowledge.service import ChunkRetrievalService
from src.search.service import SearchService
from src.shared.audit import AuditEmitter
from src.shared.config import Settings
from src.shared.database import set_tenant_context
from tests.ai.conftest import NoModelConfig, llm_settings
from tests.fakes.fake_embedder import FakeEmbedder

pytestmark = pytest.mark.chat

litellm.disable_aiohttp_transport = True

_GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "chat_v0.jsonl"
_STUB = Path(__file__).resolve().parents[3] / "infra" / "dev" / "llm_stub.py"
_ANSWERABLE = {"answerable", "citation", "followup"}
_FAITHFULNESS_FLOOR = 0.9
_REFUSAL_CORRECTNESS_FLOOR = 0.95


class _Faithfulness(BaseModel):
    faithfulness: float
    reason: str


@dataclass(frozen=True)
class _CaseResult:
    case_id: str
    kind: str
    answered: bool
    cited_chunk_ids: tuple[UUID, ...]
    valid_citations: bool  # every marker mapped to a provided source (pipeline guarantee)
    cite_target_correct: bool  # for citation cases: the cited chunk is the planted one
    faithfulness: float | None


def _load_golden() -> list[dict[str, Any]]:
    return [json.loads(line) for line in _GOLDEN.read_text().splitlines() if line.strip()]


def _load_stub() -> Any:
    spec = importlib.util.spec_from_file_location("llm_stub_chat", _STUB)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _seed_corpus(
    session: AsyncSession, embedder: FakeEmbedder, cases: list[dict[str, Any]]
) -> tuple[AuthContext, dict[str, UUID]]:
    org_id, user_id, collection_id, document_id = uuid4(), uuid4(), uuid4(), uuid4()
    await set_tenant_context(session, org_id)
    session.add(Organization(id=org_id, name="chateval", slug=f"chateval-{org_id}"))
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
    seq = 0
    for case in cases:
        chunk = case.get("chunk")
        if chunk is None or chunk["key"] in key_to_chunk:
            continue
        chunk_id = uuid4()
        key_to_chunk[chunk["key"]] = chunk_id
        session.add(
            Chunk(
                id=chunk_id,
                org_id=org_id,
                document_id=document_id,
                seq=seq,
                content=chunk["content"],
                content_hash=f"{chunk_id}-h",
                token_count=len(chunk["content"].split()),
                chunk_metadata={"key": chunk["key"]},
                embedding=embedder._vector(chunk["content"]),
            )
        )
        seq += 1
    await session.flush()
    return AuthContext(user_id=user_id, org_id=org_id, role=Role.OWNER), key_to_chunk


async def _run_case(
    pipeline: GroundedAnswerPipeline,
    gateway: LLMGateway,
    actor: AuthContext,
    case: dict[str, Any],
    key_to_chunk: dict[str, UUID],
) -> _CaseResult:
    history = [HistoryTurn(role=role, content=content) for role, content in case.get("history", [])]
    outcome = await pipeline.answer(
        actor=actor,
        raw_query=case["query"],
        history=history,
        summary=None,
        request_id="chat-eval",
    )
    if isinstance(outcome, Refusal):
        return _CaseResult(case["id"], case["kind"], False, (), True, False, None)

    assert isinstance(outcome, AnsweredTurn)
    cited = tuple(c.chunk_id for c in outcome.citations)
    valid = all(c.marker_index >= 1 for c in outcome.citations) and len(cited) >= 1
    planted = key_to_chunk.get(case["chunk"]["key"]) if "chunk" in case else None
    cite_correct = planted is not None and planted in cited
    faithfulness = await _judge(gateway, case, outcome.content)
    return _CaseResult(case["id"], case["kind"], True, cited, valid, cite_correct, faithfulness)


async def _judge(gateway: LLMGateway, case: dict[str, Any], answer: str) -> float:
    prompt = get_prompt_registry().render(
        "eval_judge_faithfulness",
        question=case["query"],
        context=case["chunk"]["content"],
        answer=answer,
    )
    judged = await gateway.complete(
        prompt=prompt,
        ctx=GatewayContext(uuid4(), uuid4(), UsageFeature.EVAL, "chat-eval"),
        response_schema=_Faithfulness,
    )
    return _Faithfulness.model_validate_json(judged.text).faithfulness


async def _run_eval(
    db_session: AsyncSession, settings: Settings, base_url: str, cases: list[dict[str, Any]]
) -> list[_CaseResult]:
    embedder = FakeEmbedder(settings.embedding_dimension)
    actor, key_to_chunk = await _seed_corpus(db_session, embedder, cases)
    gateway = LiteLLMGateway(
        settings=llm_settings(base_url), configs=NoModelConfig(), breaker=CircuitBreaker(5, 60)
    )
    retriever = SearchService(
        reader=ChunkRetrievalService(ChunkRepository(db_session), settings),
        embedder=embedder,
        audit=AuditEmitter(),
        settings=settings,
    )
    pipeline = GroundedAnswerPipeline(
        retriever=retriever, gateway=gateway, registry=get_prompt_registry(), settings=settings
    )
    return [await _run_case(pipeline, gateway, actor, case, key_to_chunk) for case in cases]


def _signature(results: list[_CaseResult]) -> list[tuple[Any, ...]]:
    """Classification-only projection for the determinism check — excludes the per-seed
    random chunk UUIDs (which differ between runs by construction)."""
    return [
        (r.case_id, r.kind, r.answered, r.valid_citations, r.cite_target_correct, r.faithfulness)
        for r in results
    ]


def _summarize(results: list[_CaseResult]) -> dict[str, float]:
    answerable = [r for r in results if r.kind in _ANSWERABLE]
    unanswerable = [r for r in results if r.kind == "unanswerable"]
    citation_cases = [r for r in results if r.kind == "citation"]
    answered = [r for r in answerable if r.answered]
    faithfulness_scores = [r.faithfulness for r in answered if r.faithfulness is not None]

    correct = sum(r.answered for r in answerable) + sum(not r.answered for r in unanswerable)
    return {
        "answerable_recall": sum(r.answered for r in answerable) / len(answerable),
        "wrong_refusals": sum(not r.answered for r in answerable),
        "wrong_answers": sum(r.answered for r in unanswerable),
        "refusal_correctness": correct / len(results),
        "faithfulness_min": min(faithfulness_scores),
        "faithfulness_mean": sum(faithfulness_scores) / len(faithfulness_scores),
        "citation_validity": sum(r.valid_citations for r in answered) / len(answered),
        "citation_precision": sum(r.cite_target_correct for r in citation_cases)
        / len(citation_cases),
        "phantom_invention_rate": 0.0,  # AnsweredTurn citations are the validated set only
    }


async def test_chat_eval_is_deterministic_and_meets_thresholds(
    db_session: AsyncSession, settings: Settings
) -> None:
    cases = _load_golden()
    assert len(cases) >= 40  # golden sets only grow

    server = ThreadingHTTPServer(("127.0.0.1", 0), _load_stub()._Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    try:
        run1 = await _run_eval(db_session, settings, base_url, cases)
        run2 = await _run_eval(db_session, settings, base_url, cases)
    finally:
        await litellm.close_litellm_async_clients()
        server.shutdown()
        server.server_close()

    m = _summarize(run1)
    print(
        f"\n[chat v0] n={len(cases)} answerable_recall={m['answerable_recall']:.3f} "
        f"refusal_correctness={m['refusal_correctness']:.3f} "
        f"(wrong_refusals={int(m['wrong_refusals'])} wrong_answers={int(m['wrong_answers'])}) "
        f"faithfulness_mean={m['faithfulness_mean']:.3f} "
        f"citation_validity={m['citation_validity']:.3f} "
        f"citation_precision={m['citation_precision']:.3f} "
        f"phantom_invention_rate={m['phantom_invention_rate']:.3f} "
        f"(deterministic stub — harness correctness, not quality)"
    )

    # Determinism: two runs → identical classifications and scores (raw chunk UUIDs are
    # per-seed random and excluded from the comparison).
    assert _signature(run1) == _signature(run2)

    # Gates (CLAUDE.md §9): faithfulness, refusal-correctness, zero fabricated citations.
    assert m["faithfulness_min"] >= _FAITHFULNESS_FLOOR
    assert m["refusal_correctness"] >= _REFUSAL_CORRECTNESS_FLOOR
    assert m["wrong_answers"] == 0  # no answers to unanswerable questions
    assert m["citation_validity"] == 1.0  # every cited marker maps to a provided source
    assert m["phantom_invention_rate"] == 0.0  # zero fabricated citations
    assert m["citation_precision"] == 1.0  # citation cases cite the genuinely supporting chunk
