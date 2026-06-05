"""Table-aware chunking: row-group chunks that repeat the header for context."""

from src.knowledge.chunking.base import ChunkParams, ChunkPiece, make_piece
from src.knowledge.parsing.base import ContentBlock, ParsedDocument


class TableAwareChunking:
    """Splits each table block into groups of ``table_rows`` data rows, repeating
    the header row in every chunk so a row group is never read without its column
    context. Non-table blocks (e.g. prose in a table-heavy DOCX) pass through as a
    single chunk each.
    """

    def __init__(self, params: ChunkParams) -> None:
        self._rows = max(1, params.table_rows)

    def chunk(self, parsed: ParsedDocument) -> list[ChunkPiece]:
        """Time: O(rows) over every table. Space: O(one row group)."""
        pieces: list[ChunkPiece] = []
        for block in parsed.blocks:
            if block.is_table:
                pieces.extend(self._chunk_table(block))
            else:
                pieces.append(make_piece(block.text, block.ref))
        return pieces

    def _chunk_table(self, block: ContentBlock) -> list[ChunkPiece]:
        lines = block.text.split("\n")
        header, data = lines[0], lines[1:]
        if not data:
            return [make_piece(header, block.ref)]
        pieces: list[ChunkPiece] = []
        for start in range(0, len(data), self._rows):
            group = data[start : start + self._rows]
            pieces.append(make_piece("\n".join([header, *group]), block.ref))
        return pieces
