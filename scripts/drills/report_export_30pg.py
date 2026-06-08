#!/usr/bin/env python
"""30-page report export timing drill (Phase 5, Task 7).

Renders a 30+ page report to PDF (the deterministic export render, ADR 0018), asserts the
< 10 min budget, and proves — by extracting the PDF text with PyMuPDF — that citations render
as notes and the generation-metadata footer is present. Generation (the LangGraph graph) is
stub-fast; the export render is the measured work. Run standalone (NOT under pytest — PyMuPDF
segfaults with the pytest-asyncio plugin active, ADR 0008):

    bash scripts/drills/report_export_30pg.sh
"""

from __future__ import annotations

import time

import fitz

from src.reports.pdf import render_report_pdf

_BUDGET_SECONDS = 600
_SECTIONS = 40
_BODY = "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor. " * 60


def main() -> int:
    citations = [
        {
            "marker_index": i + 1,
            "chunk_id": f"c{i}",
            "document_id": f"d{i}",
            "collection_id": "col",
            "filename": f"source-{i}.pdf",
            "page_ref": i + 1,
        }
        for i in range(_SECTIONS)
    ]
    sections = [
        {"heading": f"Section {i + 1}", "body": _BODY, "citation_markers": [i + 1]}
        for i in range(_SECTIONS)
    ]
    metadata = {
        "model": "openai/stub",
        "prompt_version": "1.0.0",
        "generated_at": "2026-06-08T00:00:00Z",
        "source_document_ids": ["d0"],
    }

    start = time.perf_counter()
    pdf = render_report_pdf(
        title="30-Page Report", sections=sections, citations=citations, metadata=metadata
    )
    elapsed = time.perf_counter() - start

    doc = fitz.open(stream=pdf, filetype="pdf")
    text = "".join(page.get_text() for page in doc)
    pages = doc.page_count
    citations_render = "[1] source-0.pdf" in text
    metadata_footer = "openai/stub" in text and "prompt 1.0.0" in text

    print(
        f"\n[report export] pages={pages} size={len(pdf) // 1024}KB "
        f"render={elapsed * 1000:.1f}ms (budget < {_BUDGET_SECONDS}s)\n"
        f"  citations_render={citations_render} metadata_footer={metadata_footer}\n"
        f"  (deterministic fpdf2 render; generation is stub-fast — real-model generation time "
        f"is re-measured when a provider is configured)"
    )

    ok = (
        pages >= 30
        and elapsed < _BUDGET_SECONDS
        and citations_render
        and metadata_footer
    )
    if not ok:
        print("  BUDGET/CONTENT FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
