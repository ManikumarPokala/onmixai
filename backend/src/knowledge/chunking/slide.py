"""Slide chunking: one chunk per slide, combining its body and speaker notes."""

from src.knowledge.chunking.base import ChunkParams, ChunkPiece, make_piece
from src.knowledge.parsing.base import ParsedDocument


class SlideChunking:
    """Groups every block sharing a ``slide`` ref (body + notes) into one chunk,
    in first-seen slide order. A slide is the natural retrieval unit for a deck,
    so it is kept whole regardless of token count.
    """

    def __init__(self, params: ChunkParams) -> None:
        self._params = params  # uniform constructor; slide chunking has no tunables

    def chunk(self, parsed: ParsedDocument) -> list[ChunkPiece]:
        """Time: O(blocks). Space: O(slides)."""
        groups: dict[int, list[str]] = {}
        order: list[int] = []
        for block in parsed.blocks:
            slide = int(block.ref.get("slide", 0))
            if slide not in groups:
                groups[slide] = []
                order.append(slide)
            groups[slide].append(block.text)
        return [make_piece("\n\n".join(groups[slide]), {"slide": slide}) for slide in order]
