"""PDF render text-extraction proof — runs in the asyncio-free pass (PyMuPDF segfaults under
the pytest-asyncio plugin; ADR 0008). Proves the rendered PDF carries the sections, the cited
sources as notes, and the generation-metadata footer, and is byte-deterministic + multi-page.
"""

import fitz

from src.reports.pdf import render_report_pdf

_CITATION = {
    "marker_index": 1,
    "chunk_id": "c",
    "document_id": "d",
    "collection_id": "col",
    "filename": "guide.pdf",
    "page_ref": 7,
}
_METADATA = {"model": "openai/stub", "prompt_version": "1.0.0", "generated_at": "2026-06-08"}


def _text(pdf: bytes) -> str:
    return "\n".join(page.get_text() for page in fitz.open(stream=pdf, filetype="pdf"))


def test_pdf_contains_sections_citations_and_metadata_footer() -> None:
    pdf = render_report_pdf(
        title="Q3 Review",
        sections=[{"heading": "Overview", "body": "Revenue grew.", "citation_markers": [1]}],
        citations=[_CITATION],
        metadata=_METADATA,
    )
    text = _text(pdf)
    assert "Overview" in text and "Revenue grew" in text  # the section
    assert "[1] guide.pdf, p.7" in text  # citation rendered as a note
    assert "openai/stub" in text and "prompt 1.0.0" in text  # generation-metadata footer


def test_pdf_is_byte_deterministic() -> None:
    def render() -> bytes:
        return render_report_pdf(
            title="T",
            sections=[{"heading": "H", "body": "B", "citation_markers": [1]}],
            citations=[_CITATION],
            metadata=_METADATA,
        )

    assert render() == render()  # same input → same bytes


def test_large_report_renders_multiple_pages() -> None:
    sections = [
        {"heading": f"Section {i}", "body": "Body text. " * 50, "citation_markers": [1]}
        for i in range(40)
    ]
    pdf = render_report_pdf(
        title="Big report", sections=sections, citations=[_CITATION], metadata=_METADATA
    )
    assert fitz.open(stream=pdf, filetype="pdf").page_count >= 5
