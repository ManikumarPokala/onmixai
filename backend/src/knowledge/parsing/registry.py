"""Content-type → parser registry."""

from src.knowledge.ingest_errors import ParserError
from src.knowledge.parsing.base import OcrEngine, ParsedDocument, Parser
from src.knowledge.parsing.docx import DocxParser
from src.knowledge.parsing.pdf import PdfParser
from src.knowledge.parsing.pptx import PptxParser
from src.knowledge.parsing.txt import TxtParser
from src.knowledge.parsing.xlsx import XlsxParser

_PDF = "application/pdf"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_TXT = "text/plain"


class ParserRegistry:
    """Routes a document's bytes to the parser for its content type."""

    def __init__(self, ocr: OcrEngine) -> None:
        self._parsers: dict[str, Parser] = {
            _PDF: PdfParser(ocr),
            _DOCX: DocxParser(),
            _PPTX: PptxParser(),
            _XLSX: XlsxParser(),
            _TXT: TxtParser(),
        }

    def parse(self, content_type: str, data: bytes, *, max_pages: int) -> ParsedDocument:
        parser = self._parsers.get(content_type)
        if parser is None:
            raise ParserError(f"no parser for content type {content_type}")
        return parser.parse(data, max_pages=max_pages)
