"""Typed state for the fixed knowledge→report graph (ADR 0017). The graph is linear:
knowledge_agent → report_agent → END. ``error`` is a typed TERMINAL outcome carried in the
state (INSUFFICIENT_EVIDENCE from node 1, NO_GROUNDED_SECTIONS from node 2) — never an
exception escaping the graph. total=False so the entry node only needs the inputs."""

from typing import Any, TypedDict

from src.search.schemas import SearchResultItem


class ReportState(TypedDict, total=False):
    # --- inputs ---
    query: str
    collection_scope: list[str]  # collection ids (as strings) the report is scoped to
    report_type: str  # ReportType value
    request_id: str
    # --- filled by node 1 (knowledge_agent) ---
    retrieved: list[SearchResultItem]
    grounded_context: str  # numbered, guardrail-framed sources block
    # --- filled by node 2 (report_agent) ---
    sections: list[dict[str, Any]]  # validated ReportSection dicts
    citations: list[dict[str, Any]]  # resolved citations (marker → source)
    metadata: dict[str, Any]  # model, prompt_version, source_document_ids
    trace_id: str | None
    # --- typed terminal outcome (a content decline, not an error) ---
    error: str | None
