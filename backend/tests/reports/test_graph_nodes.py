"""The two report-graph nodes, exercised in isolation.

node 1 (knowledge_agent): retrieval + assembly with NO gateway; too-few sources → the typed
INSUFFICIENT_EVIDENCE terminal. node 2 (report_agent, FakeGateway): structured sections,
phantom-marker strip, all-sections-unsupported → NO_GROUNDED_SECTIONS, and pass-through (no
generation spend) when node 1 already declined.
"""

import json
from uuid import uuid4

from src.ai.prompt_registry import get_prompt_registry
from src.identity.models import Role
from src.identity.schemas import AuthContext
from src.reports.graph.nodes import knowledge_agent, report_agent
from src.reports.graph.state import ReportState
from src.search.schemas import SearchQuery, SearchResult, SearchResultItem, SourceAttribution
from tests.ai.conftest import llm_settings
from tests.fakes.fake_gateway import FakeGateway


def _item(content: str) -> SearchResultItem:
    return SearchResultItem(
        chunk_id=uuid4(),
        content=content,
        score=0.5,
        source=SourceAttribution(
            document_id=uuid4(), collection_id=uuid4(), filename="doc.txt", ref={"page": 1}
        ),
    )


class _FakeRetriever:
    def __init__(self, items: list[SearchResultItem]) -> None:
        self._items = items

    async def search(self, actor: AuthContext, query: SearchQuery) -> SearchResult:
        return SearchResult(results=self._items, next_cursor=None)


def _actor() -> AuthContext:
    return AuthContext(user_id=uuid4(), org_id=uuid4(), role=Role.MEMBER)


def _report_json(sections: list[dict[str, object]]) -> str:
    return json.dumps({"sections": sections})


def _section(markers: list[int], heading: str = "Overview") -> dict[str, object]:
    return {"heading": heading, "body": "A grounded body.", "citation_markers": markers}


def _base_state(retrieved: list[SearchResultItem]) -> ReportState:
    return {
        "query": "summarize the program",
        "report_type": "executive_summary",
        "request_id": "r",
        "retrieved": retrieved,
        "grounded_context": "\n".join(f"[{i + 1}] {it.content}" for i, it in enumerate(retrieved)),
        "error": None,
    }


# --- node 1 ---


async def test_knowledge_agent_retrieves_and_assembles() -> None:
    retriever = _FakeRetriever([_item("source one"), _item("source two")])
    out = await knowledge_agent(
        {"query": "q", "collection_scope": [], "report_type": "technical"},
        retriever=retriever,
        actor=_actor(),
        settings=llm_settings("http://x"),
    )
    assert out["error"] is None
    assert len(out["retrieved"]) == 2
    assert "[1]" in out["grounded_context"] and "[2]" in out["grounded_context"]


async def test_knowledge_agent_declines_on_too_few_sources() -> None:
    retriever = _FakeRetriever([_item("only one")])  # 1 < report_min_sources (2)
    out = await knowledge_agent(
        {"query": "q", "collection_scope": [], "report_type": "technical"},
        retriever=retriever,
        actor=_actor(),
        settings=llm_settings("http://x"),
    )
    assert out["error"] == "INSUFFICIENT_EVIDENCE"
    assert out["retrieved"] == []


# --- node 2 ---


async def test_report_agent_produces_structured_grounded_sections() -> None:
    gateway = FakeGateway()
    gateway.queue_completion(text=_report_json([_section([1]), _section([2], "Findings")]))
    out = await report_agent(
        _base_state([_item("s1"), _item("s2")]),
        gateway=gateway,
        registry=get_prompt_registry(),
        actor=_actor(),
    )
    assert out["error"] is None
    assert [s["heading"] for s in out["sections"]] == ["Overview", "Findings"]
    assert out["metadata"]["prompt_version"] == "1.0.0"
    assert len(out["metadata"]["source_document_ids"]) == 2
    assert {c["marker_index"] for c in out["citations"]} == {1, 2}


async def test_report_agent_strips_phantom_marker() -> None:
    gateway = FakeGateway()
    gateway.queue_completion(text=_report_json([_section([1, 9])]))  # 2 sources → 9 phantom
    out = await report_agent(
        _base_state([_item("s1"), _item("s2")]),
        gateway=gateway,
        registry=get_prompt_registry(),
        actor=_actor(),
    )
    assert out["error"] is None
    assert out["sections"][0]["citation_markers"] == [1]


async def test_report_agent_fails_when_no_section_survives() -> None:
    gateway = FakeGateway()
    gateway.queue_completion(text=_report_json([_section([9])]))  # only phantom → dropped
    out = await report_agent(
        _base_state([_item("s1")]),
        gateway=gateway,
        registry=get_prompt_registry(),
        actor=_actor(),
    )
    assert out["error"] == "NO_GROUNDED_SECTIONS"
    assert "sections" not in out


async def test_report_agent_passes_through_when_upstream_declined() -> None:
    gateway = FakeGateway()
    state = _base_state([])
    state["error"] = "INSUFFICIENT_EVIDENCE"
    out = await report_agent(state, gateway=gateway, registry=get_prompt_registry(), actor=_actor())
    assert out == {}  # no sections produced
    assert gateway.calls == []  # and no generation spend
