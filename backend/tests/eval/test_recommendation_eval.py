"""Recommendation golden-set v0 — the Phase-5 recommendation regression gate. Runs every
golden case through the REAL ``RecommendationPipeline`` (retrieval scores → confidence band →
decline gate → structured generation → justification grounding) with the two ports faked
deterministically: a settable retriever (the case's fused scores) and a scripted gateway (the
case's model output). With the fakes the outcome is a fixed, repeatable value, so this gate
proves the harness + pipeline are correct and deterministic — NOT model quality (the honesty
caveat in ADR 0013/0016, mirroring the retrieval + generation golden sets).

What it gates (per the Task-9 spec):
- schema-validity rate (100%): every scripted model output validates against the strict
  ``RecommendationOutput`` (``extra="forbid"``, ≥1 marker per justification).
- decline-correctness (100%): an answerable case never declines; an insufficient case always
  declines; a below-floor/empty case declines BEFORE generation (zero model spend).
- justification-grounding + zero fabricated citations: every completed outcome's resolved
  citations point only to a retrieved source — phantom markers are stripped, never surfaced.
- phantom rate: the pre-strip invention rate, reported as a quality signal (not gated to 0;
  the ``phantom_survive`` cases deliberately carry a stripped phantom).

The confidence MONOTONICITY property (exit criterion 1) lives in
``tests/recommendation/test_confidence_property.py`` and runs in the unit suite too.
"""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from src.ai.prompt_registry import get_prompt_registry
from src.identity.models import Role
from src.identity.schemas import AuthContext
from src.recommendation.pipeline import (
    CompletedRecommendation,
    Declined,
    RecommendationPipeline,
)
from src.recommendation.schemas import RecommendationOutput
from src.search.schemas import SearchQuery, SearchResult, SearchResultItem, SourceAttribution
from tests.ai.conftest import llm_settings
from tests.fakes.fake_gateway import FakeGateway

pytestmark = pytest.mark.recommendation

_GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "recommendation_v0.jsonl"


def _load_golden() -> list[dict[str, Any]]:
    return [json.loads(line) for line in _GOLDEN.read_text().splitlines() if line.strip()]


class _ScriptedRetriever:
    """A settable stand-in for the permission-aware retriever (the Retriever port)."""

    def __init__(self) -> None:
        self.results: list[SearchResultItem] = []

    def set_scores(self, scores: list[float]) -> None:
        self.results = [
            SearchResultItem(
                chunk_id=uuid4(),
                content=f"retrieved evidence fragment {i + 1}",
                score=score,
                source=SourceAttribution(
                    document_id=uuid4(),
                    collection_id=uuid4(),
                    filename=f"doc-{i + 1}.pdf",
                    ref={"page": i + 1},
                ),
            )
            for i, score in enumerate(scores)
        ]

    async def search(self, actor: AuthContext, query: SearchQuery) -> SearchResult:
        return SearchResult(results=self.results, next_cursor=None)


def _actor() -> AuthContext:
    return AuthContext(user_id=uuid4(), org_id=uuid4(), role=Role.MEMBER)


async def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    """Drive one golden case through the real pipeline; return its measured outcome."""
    retriever = _ScriptedRetriever()
    retriever.set_scores(case["scores"])
    gateway = FakeGateway()

    schema_valid = True
    model_output = case.get("model_output")
    if model_output is not None:
        # Schema-validity is the gate: the scripted output must satisfy the strict contract.
        try:
            RecommendationOutput.model_validate(model_output)
        except Exception:  # noqa: BLE001 — the eval records the failure as a metric, not a crash
            schema_valid = False
        gateway.queue_completion(text=json.dumps(model_output))

    pipeline = RecommendationPipeline(
        retriever=retriever,
        gateway=gateway,
        registry=get_prompt_registry(),
        settings=llm_settings("http://eval"),
    )
    outcome = await pipeline.recommend(
        actor=_actor(), query=case["query"], collection_scope=[], request_id="eval"
    )

    valid_markers = set(range(1, len(case["scores"]) + 1))
    original_markers = (
        [m for j in model_output["justifications"] for m in j["citation_markers"]]
        if model_output is not None
        else []
    )
    phantom = sum(1 for m in original_markers if m not in valid_markers)

    if isinstance(outcome, CompletedRecommendation):
        surfaced = {c.marker_index for c in outcome.citations}
        return {
            "status": "completed",
            "band": outcome.band,
            "schema_valid": schema_valid,
            "fabricated_citations": sum(1 for m in surfaced if m not in valid_markers),
            "phantom": phantom,
            "total_markers": len(original_markers),
            "gateway_called": len(gateway.calls) > 0,
        }
    assert isinstance(outcome, Declined)
    return {
        "status": "declined",
        "reason": outcome.reason,
        "schema_valid": schema_valid,
        "fabricated_citations": 0,
        "phantom": phantom,
        "total_markers": len(original_markers),
        "gateway_called": len(gateway.calls) > 0,
    }


async def _run_eval() -> list[dict[str, Any]]:
    return [await _run_case(case) for case in _load_golden()]


async def test_recommendation_eval_is_deterministic_and_meets_thresholds() -> None:
    golden = _load_golden()
    assert len(golden) >= 30

    run1 = await _run_eval()
    run2 = await _run_eval()
    assert run1 == run2  # two runs → identical outcomes (determinism)

    schema_rate = sum(1 for r in run1 if r["schema_valid"]) / len(run1)
    decline_correct = 0
    fabricated = sum(r["fabricated_citations"] for r in run1)
    total_markers = sum(r["total_markers"] for r in run1)
    phantom_total = sum(r["phantom"] for r in run1)

    for case, result in zip(golden, run1, strict=True):
        want = case["expect"]
        assert result["status"] == want["status"], case["id"]
        if want["status"] == "completed":
            assert result["band"] is not None, case["id"]
            if want.get("band"):  # an answerable case pins its exact band
                assert result["band"] == want["band"], case["id"]
        else:
            assert result["reason"] == want["reason"], case["id"]
        # An insufficient (below-floor/empty) case must decline BEFORE any generation spend.
        if case["kind"] == "insufficient":
            assert result["gateway_called"] is False, case["id"]
        decline_correct += 1

    decline_rate = decline_correct / len(golden)
    phantom_rate = phantom_total / total_markers if total_markers else 0.0

    print(
        f"\n[recommendation v0] cases={len(golden)} schema_valid={schema_rate:.3f} "
        f"decline_correct={decline_rate:.3f} fabricated_citations={fabricated} "
        f"phantom_rate={phantom_rate:.3f} (deterministic fakes — harness correctness, not quality)"
    )

    assert schema_rate == 1.0  # gate: every scripted output is schema-valid
    assert decline_rate == 1.0  # gate: every outcome matches its expected decision
    assert fabricated == 0  # gate: zero fabricated citations ever surface (phantoms stripped)
