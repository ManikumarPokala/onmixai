"""The fixed knowledge→report graph end-to-end (assembled once, exercised via ainvoke):
a query → grounded report content; low retrieval → declined terminal (no generation); an
all-phantom generation → NO_GROUNDED_SECTIONS terminal. Errors are typed terminal STATE,
never exceptions escaping the graph.
"""

import json
from uuid import uuid4

from src.ai.prompt_registry import get_prompt_registry
from src.identity.models import Role
from src.identity.schemas import AuthContext
from src.reports.graph.graph import build_report_graph
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


def _graph(retriever: _FakeRetriever, gateway: FakeGateway):  # type: ignore[no-untyped-def]
    return build_report_graph(
        retriever=retriever,
        gateway=gateway,
        registry=get_prompt_registry(),
        actor=AuthContext(user_id=uuid4(), org_id=uuid4(), role=Role.MEMBER),
        settings=llm_settings("http://x"),
    )


def _initial(report_type: str = "executive_summary") -> ReportState:
    return {
        "query": "summarize",
        "collection_scope": [],
        "report_type": report_type,
        "request_id": "r",
    }


def _report_json(markers: list[int]) -> str:
    return json.dumps(
        {"sections": [{"heading": "Overview", "body": "grounded.", "citation_markers": markers}]}
    )


async def test_full_graph_produces_grounded_report() -> None:
    gateway = FakeGateway()
    gateway.queue_completion(text=_report_json([1]))
    graph = _graph(_FakeRetriever([_item("s1"), _item("s2")]), gateway)
    final: ReportState = await graph.ainvoke(_initial())
    assert final.get("error") is None
    assert final["sections"][0]["heading"] == "Overview"
    assert final["citations"]
    assert final["metadata"]["source_document_ids"]


async def test_full_graph_declines_on_low_retrieval() -> None:
    gateway = FakeGateway()
    graph = _graph(_FakeRetriever([_item("only one")]), gateway)  # 1 < min 2
    final: ReportState = await graph.ainvoke(_initial("technical"))
    assert final["error"] == "INSUFFICIENT_EVIDENCE"
    assert not final.get("sections")
    assert gateway.calls == []  # node 2 passed through — no generation spend


async def test_full_graph_terminal_when_no_section_survives() -> None:
    gateway = FakeGateway()
    gateway.queue_completion(text=_report_json([9]))  # all-phantom → no section survives
    graph = _graph(_FakeRetriever([_item("s1"), _item("s2")]), gateway)
    final: ReportState = await graph.ainvoke(_initial())
    assert final["error"] == "NO_GROUNDED_SECTIONS"
    assert not final.get("sections")
