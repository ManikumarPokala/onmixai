"""Report golden-set v0 — the Phase-5 report regression gate. Runs every golden case through
the REAL fixed knowledge→report graph (``build_report_graph`` → ``ainvoke``) with the two
ports faked deterministically: a retriever returning the case's source count, and a scripted
gateway returning the case's structured content. The outcome is a fixed, repeatable terminal
state, so this gate proves the graph + grounding are correct and deterministic — NOT model
quality (the honesty caveat, mirroring the recommendation/generation golden sets).

What it gates (per the Task-9 spec):
- decline-correctness (100%): each case reaches its expected typed terminal — a too-thin
  corpus → ``INSUFFICIENT_EVIDENCE`` (no generation spend); an all-phantom generation →
  ``NO_GROUNDED_SECTIONS`` (FAILED with reason, never an empty-but-successful report); a
  grounded generation → a content report.
- section-citation validity (100% of ready reports): every surfaced section carries ≥1
  citation marker that resolves to a retrieved source — no fabricated citation survives.
- metadata completeness (100% of ready reports): model + prompt_version + source_document_ids
  are all present.
- the NO_GROUNDED_SECTIONS terminal is exercised EXPLICITLY (a report whose every section
  loses its citations must end failed with a reason, never an empty success).
"""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from src.ai.prompt_registry import get_prompt_registry
from src.identity.models import Role
from src.identity.schemas import AuthContext
from src.reports.graph.graph import build_report_graph
from src.reports.graph.state import ReportState
from src.search.schemas import SearchQuery, SearchResult, SearchResultItem, SourceAttribution
from tests.ai.conftest import llm_settings
from tests.fakes.fake_gateway import FakeGateway

pytestmark = pytest.mark.report

_GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "report_v0.jsonl"


def _load_golden() -> list[dict[str, Any]]:
    return [json.loads(line) for line in _GOLDEN.read_text().splitlines() if line.strip()]


class _ScriptedRetriever:
    def __init__(self, num_sources: int) -> None:
        self._items = [
            SearchResultItem(
                chunk_id=uuid4(),
                content=f"source {i + 1}",
                score=0.5,
                source=SourceAttribution(
                    document_id=uuid4(),
                    collection_id=uuid4(),
                    filename=f"doc-{i + 1}.pdf",
                    ref={"page": i + 1},
                ),
            )
            for i in range(num_sources)
        ]

    async def search(self, actor: AuthContext, query: SearchQuery) -> SearchResult:
        return SearchResult(results=self._items, next_cursor=None)


def _actor() -> AuthContext:
    return AuthContext(user_id=uuid4(), org_id=uuid4(), role=Role.MEMBER)


async def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    """Drive one golden case through the real graph; return its measured terminal."""
    retriever = _ScriptedRetriever(case["num_sources"])
    gateway = FakeGateway()
    if case.get("model_output") is not None:
        gateway.queue_completion(text=json.dumps(case["model_output"]))

    graph = build_report_graph(
        retriever=retriever,
        gateway=gateway,
        registry=get_prompt_registry(),
        actor=_actor(),
        settings=llm_settings("http://eval"),
    )
    initial: ReportState = {
        "query": case["query"],
        "collection_scope": [],
        "report_type": case["report_type"],
        "request_id": "eval",
    }
    final: ReportState = await graph.ainvoke(initial)

    valid_markers = set(range(1, case["num_sources"] + 1))
    sections = final.get("sections") or []
    citations = final.get("citations") or []
    metadata = final.get("metadata") or {}
    fabricated = sum(1 for s in sections for m in s["citation_markers"] if m not in valid_markers)
    metadata_complete = all(
        bool(metadata.get(k)) for k in ("model", "prompt_version", "source_document_ids")
    )
    every_section_cited = all(s["citation_markers"] for s in sections)
    citations_resolve = all(c["marker_index"] in valid_markers for c in citations)

    return {
        "error": final.get("error"),
        "num_sections": len(sections),
        "fabricated_citations": fabricated,
        "metadata_complete": metadata_complete if final.get("error") is None else None,
        "section_citation_valid": every_section_cited and citations_resolve,
        "gateway_called": len(gateway.calls) > 0,
    }


async def _run_eval() -> list[dict[str, Any]]:
    return [await _run_case(case) for case in _load_golden()]


async def test_report_eval_is_deterministic_and_meets_thresholds() -> None:
    golden = _load_golden()
    assert len(golden) >= 15

    run1 = await _run_eval()
    run2 = await _run_eval()
    assert run1 == run2  # two runs → identical terminals (determinism)

    fabricated = sum(r["fabricated_citations"] for r in run1)
    no_grounded_seen = 0

    for case, result in zip(golden, run1, strict=True):
        want = case["expect"]
        assert result["error"] == want["error"], case["id"]

        if want["error"] is None:  # a ready report
            assert result["num_sections"] >= want["min_sections"], case["id"]
            assert result["section_citation_valid"], case["id"]
            assert result["metadata_complete"], case["id"]
        else:  # a typed content decline → no sections, never an empty success
            assert result["num_sections"] == 0, case["id"]

        if case["kind"] == "insufficient":
            assert result["gateway_called"] is False, case["id"]  # no generation spend
        if want["error"] == "NO_GROUNDED_SECTIONS":
            no_grounded_seen += 1
            assert result["gateway_called"] is True, case["id"]  # generated, then all dropped

    print(
        f"\n[report v0] cases={len(golden)} no_grounded_terminals={no_grounded_seen} "
        f"fabricated_citations={fabricated} (deterministic fakes — harness correctness, not "
        f"quality)"
    )

    # The NO_GROUNDED_SECTIONS terminal is an exit criterion — it must actually be exercised.
    assert no_grounded_seen >= 1
    assert fabricated == 0  # gate: no fabricated citation ever survives into a ready report
