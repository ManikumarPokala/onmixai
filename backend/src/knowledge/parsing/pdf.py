"""PDF parser (PyMuPDF), page-by-page, with OCR fallback for scanned pages."""

from src.knowledge.ingest_errors import ParserError
from src.knowledge.parsing.base import ContentBlock, OcrEngine, ParsedDocument


class PdfParser:
    def __init__(self, ocr: OcrEngine) -> None:
        self._ocr = ocr

    def parse(self, data: bytes, *, max_pages: int) -> ParsedDocument:
        """Extract text page by page; OCR pages with no text layer.

        Time: O(pages) single pass — text is never accumulated into one giant
        string. Space: O(one page) beyond the input bytes. Raises ParserError
        (permanent) on malformed or password-protected PDFs, or past the page cap.

        PyMuPDF is imported lazily, not at module load: its SWIG runtime cannot be
        initialized in a process where pytest-asyncio's event-loop plugin is active
        (segfaults on macOS/arm64). Keeping the import inside parse() keeps pymupdf
        out of the async test graph (the worker builds the registry but only this
        method touches PyMuPDF). See ADR 0008 and tests/knowledge/README for the split.
        """
        import pymupdf

        # PyMuPDF is lenient: forced filetype="pdf" makes it image-wrap a mislabeled
        # PNG, and it silently "repairs" a truncated file into a zero-text page. Guard
        # the header up front so a non-PDF fails with a precise reason rather than
        # degrading into an empty parse.
        if not data.startswith(b"%PDF"):
            raise ParserError("file is not a PDF")
        try:
            document = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise ParserError("file is not a readable PDF") from exc
        try:
            if document.needs_pass:
                raise ParserError("PDF is password-protected")
            if document.is_repaired:
                # MuPDF silently repairs a truncated/corrupt xref into a lossy
                # document; treat that as a permanent failure rather than indexing
                # whatever fragments survived.
                raise ParserError("PDF is corrupt or truncated")
            if document.page_count > max_pages:
                raise ParserError(f"PDF exceeds the page limit of {max_pages}")
            blocks: list[ContentBlock] = []
            for index in range(document.page_count):
                page = document.load_page(index)
                text = page.get_text("text").strip()
                if not text:
                    text = self._ocr.image_to_text(page.get_pixmap().tobytes("png")).strip()
                if text:
                    blocks.append(ContentBlock(text=text, ref={"page": index + 1}))
            if not blocks:
                # No text layer and OCR recovered nothing — a damaged or empty PDF
                # would otherwise reach READY with zero chunks (breaks the Task 8
                # READY ⇒ chunks invariant). Fail it permanently with a reason.
                raise ParserError("no extractable text in PDF")
            return ParsedDocument(blocks=tuple(blocks), page_count=document.page_count)
        finally:
            document.close()
