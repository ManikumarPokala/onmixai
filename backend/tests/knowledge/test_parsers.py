"""Parser tests: every valid fixture parses; every broken one fails with a reason."""

import time
import tracemalloc
from collections.abc import Callable

import pytest

from src.knowledge.ingest_errors import ParserError
from src.knowledge.parsing.registry import ParserRegistry
from tests.fakes.fake_ocr import FakeOcrEngine
from tests.knowledge import fixtures

_PDF = "application/pdf"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_TXT = "text/plain"

_MAX_PAGES = 2000


def _registry(ocr: FakeOcrEngine | None = None) -> ParserRegistry:
    return ParserRegistry(ocr or FakeOcrEngine())


def test_valid_pdf_parses_pages() -> None:
    parsed = _registry().parse(_PDF, fixtures.valid_pdf(2), max_pages=_MAX_PAGES)
    assert parsed.page_count == 2
    assert len(parsed.blocks) == 2
    assert "Page 1" in parsed.blocks[0].text


def test_valid_docx_parses_text_and_table() -> None:
    parsed = _registry().parse(_DOCX, fixtures.valid_docx(), max_pages=_MAX_PAGES)
    assert any(not b.is_table for b in parsed.blocks)
    assert any(b.is_table for b in parsed.blocks)
    assert parsed.table_ratio > 0


def test_valid_pptx_parses_slide_and_notes() -> None:
    parsed = _registry().parse(_PPTX, fixtures.valid_pptx(), max_pages=_MAX_PAGES)
    assert parsed.page_count == 1
    assert any(b.ref.get("kind") == "notes" for b in parsed.blocks)


def test_valid_xlsx_parses_sheets_as_tables() -> None:
    parsed = _registry().parse(_XLSX, fixtures.valid_xlsx(), max_pages=_MAX_PAGES)
    assert parsed.table_ratio == 1.0
    assert parsed.blocks[0].ref["sheet"] == "Q1"
    assert "EMEA" in parsed.blocks[0].text


def test_valid_txt_utf8_and_legacy_encoding() -> None:
    utf8 = _registry().parse(_TXT, fixtures.valid_txt_utf8(), max_pages=_MAX_PAGES)
    assert "First paragraph" in utf8.blocks[0].text
    legacy = _registry().parse(_TXT, fixtures.valid_txt_legacy(), max_pages=_MAX_PAGES)
    assert "Café" in legacy.blocks[0].text  # charset-normalizer-detected legacy encoding


def test_scanned_pdf_routes_through_ocr() -> None:
    ocr = FakeOcrEngine(text="OCR_RECOVERED_TEXT")
    parsed = _registry(ocr).parse(_PDF, fixtures.scanned_pdf(), max_pages=_MAX_PAGES)
    assert ocr.calls == 1  # the image-only page went to OCR
    assert parsed.blocks[0].text == "OCR_RECOVERED_TEXT"


def test_page_limit_is_a_permanent_parser_error() -> None:
    with pytest.raises(ParserError) as exc:
        _registry().parse(_PDF, fixtures.valid_pdf(3), max_pages=2)
    assert "page limit" in str(exc.value).lower()


@pytest.mark.parametrize(
    ("content_type", "make"),
    [
        (_PDF, fixtures.truncated_pdf),
        (_PDF, fixtures.password_pdf),
        (_PDF, fixtures.png_as_pdf),
        (_PDF, fixtures.zero_byte),
        (_DOCX, fixtures.corrupt_docx),
        (_XLSX, fixtures.corrupt_xlsx),
        (_TXT, fixtures.zero_byte),
        (_TXT, fixtures.garbage_txt),
    ],
)
def test_broken_fixtures_raise_parser_error_with_reason(
    content_type: str, make: Callable[[], bytes]
) -> None:
    with pytest.raises(ParserError) as exc:
        _registry().parse(content_type, make(), max_pages=_MAX_PAGES)
    assert str(exc.value)  # human-readable, non-empty reason


def test_unknown_content_type_rejected() -> None:
    with pytest.raises(ParserError):
        _registry().parse("image/png", b"data", max_pages=_MAX_PAGES)


def test_large_pdf_parses_bounded_and_fast() -> None:
    data = fixtures.valid_pdf(100)
    tracemalloc.start()
    start = time.monotonic()
    parsed = _registry().parse(_PDF, data, max_pages=_MAX_PAGES)
    elapsed = time.monotonic() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert parsed.page_count == 100
    assert elapsed < 30  # < 30s CPU
    assert peak < 50 * 1024 * 1024  # peak memory bounded, not O(whole doc blowup)
