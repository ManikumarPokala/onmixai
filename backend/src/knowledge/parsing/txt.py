"""Plain-text parser with UTF-8 → charset-normalizer encoding detection."""

from charset_normalizer import from_bytes

from src.knowledge.ingest_errors import ParserError
from src.knowledge.parsing.base import ContentBlock, DocumentFormat, ParsedDocument


class TxtParser:
    def parse(self, data: bytes, *, max_pages: int) -> ParsedDocument:
        """Decode text, detecting a legacy encoding when UTF-8 fails.

        Time: O(n) over the bytes. Space: O(n) for the decoded text.
        """
        if not data:
            raise ParserError("file is empty")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = self._decode_legacy(data)
        return ParsedDocument(
            blocks=(ContentBlock(text=text, ref={"page": 1}),),
            page_count=1,
            format=DocumentFormat.TXT,
        )

    def _decode_legacy(self, data: bytes) -> str:
        # charset-normalizer scores candidate encodings by language coherence, so
        # real legacy text (latin-1/cp125x) resolves while random bytes yield no
        # confident match — unlike a blind latin-1 fallback, which decodes garbage.
        best = from_bytes(data).best()
        if best is None:
            raise ParserError("could not determine the text encoding")
        return str(best)
