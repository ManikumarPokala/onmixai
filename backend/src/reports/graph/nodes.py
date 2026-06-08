"""The two graph nodes (independently unit-testable with a FakeGateway).

node 1 ``knowledge_agent`` — retrieve (permission-aware) + assemble the grounded context. NO
LLM call. On too-few sources it sets the typed terminal ``error=INSUFFICIENT_EVIDENCE``.

node 2 ``report_agent`` — structured generation via the gateway with the report-type template
and ``response_schema=ReportContent`` (JSON mode + bounded re-ask). It ground-validates every
section's citations (strip phantoms, drop an unsupported section); if no section survives it
sets ``error=NO_GROUNDED_SECTIONS``. If node 1 already declined, it passes through (no spend).

Both return partial state updates. A typed gateway error (schema-invalid after re-ask, or an
infrastructure failure) propagates — it is NOT a content decline (the Phase-4 split, reused).
"""

from typing import Any
from uuid import UUID

from src.ai.gateway import GatewayContext, LLMGateway
from src.ai.guardrails import InjectionFilter
from src.ai.models import UsageFeature
from src.ai.prompt_registry import PromptRegistry
from src.identity.schemas import AuthContext
from src.reports.graph.state import ReportState
from src.reports.models import ReportType
from src.reports.rules import ground_sections
from src.reports.schemas import TEMPLATE_FOR_TYPE, ReportContent
from src.search.schemas import SearchQuery, SearchResultItem, SourceAttribution
from src.shared.config import Settings

_INJECTION = InjectionFilter()


async def _retrieve(
    retriever: Any, actor: AuthContext, query: str, scope: list[UUID], top_k: int
) -> list[SearchResultItem]:
    if not scope:
        result = await retriever.search(actor, SearchQuery(query=query, limit=top_k))
        return list(result.results)
    merged: dict[UUID, SearchResultItem] = {}
    for collection_id in scope:
        result = await retriever.search(
            actor, SearchQuery(query=query, collection_id=collection_id, limit=top_k)
        )
        for item in result.results:
            if item.chunk_id not in merged or item.score > merged[item.chunk_id].score:
                merged[item.chunk_id] = item
    return sorted(merged.values(), key=lambda item: -item.score)[:top_k]


async def knowledge_agent(
    state: ReportState, *, retriever: Any, actor: AuthContext, settings: Settings
) -> dict[str, Any]:
    """Pure retrieval + assembly. No gateway call. Too few sources → INSUFFICIENT_EVIDENCE."""
    scope = [UUID(c) for c in state.get("collection_scope", [])]
    items = await _retrieve(
        retriever, actor, state["query"], scope, settings.report_retrieval_top_k
    )
    if len(items) < settings.report_min_sources:
        return {"retrieved": [], "error": "INSUFFICIENT_EVIDENCE"}
    context = "\n".join(
        f"[{i + 1}] {_INJECTION.neutralize(items[i].content)}" for i in range(len(items))
    )
    return {"retrieved": items, "grounded_context": context, "error": None}


async def report_agent(
    state: ReportState,
    *,
    gateway: LLMGateway,
    registry: PromptRegistry,
    actor: AuthContext,
) -> dict[str, Any]:
    """Structured generation + section grounding. Passes through if node 1 declined."""
    if state.get("error"):
        return {}  # insufficient evidence upstream — no generation spend
    kept: list[SearchResultItem] = state["retrieved"]
    template = TEMPLATE_FOR_TYPE[ReportType(state["report_type"])]
    prompt = registry.render(template, sources=state["grounded_context"], query=state["query"])
    ctx = GatewayContext(
        org_id=actor.org_id,
        user_id=actor.user_id,
        feature=UsageFeature.REPORT,
        request_id=state.get("request_id", "report"),
        source_chunk_ids=tuple(item.chunk_id for item in kept),
    )
    completion = await gateway.complete(prompt=prompt, ctx=ctx, response_schema=ReportContent)
    content = ReportContent.model_validate_json(completion.text)

    grounded = ground_sections(content, frozenset(range(1, len(kept) + 1)))
    if grounded.sections is None:
        return {"error": "NO_GROUNDED_SECTIONS"}  # no section survived → fail (content decline)

    markers = sorted({m for section in grounded.sections for m in section.citation_markers})
    citations = [_citation_dict(kept[m - 1], m) for m in markers]
    source_document_ids = list(dict.fromkeys(str(item.source.document_id) for item in kept))
    return {
        "sections": [section.model_dump() for section in grounded.sections],
        "citations": citations,
        "metadata": {
            "model": completion.model_used,
            "prompt_version": prompt.template_version,
            "source_document_ids": source_document_ids,
        },
        "trace_id": completion.trace_id,
        "error": None,
    }


def _citation_dict(item: SearchResultItem, marker_index: int) -> dict[str, Any]:
    source: SourceAttribution = item.source
    page = source.ref.get("page")
    return {
        "marker_index": marker_index,
        "chunk_id": str(item.chunk_id),
        "document_id": str(source.document_id),
        "collection_id": str(source.collection_id),
        "filename": source.filename,
        "page_ref": page if isinstance(page, int) else None,
    }
