"""Chunking strategy + selection tests — pure, no I/O (and no PyMuPDF, so these
run in the normal async suite, unlike the parser tests)."""

import pytest

from src.knowledge.chunking import (
    ChunkParams,
    ProseChunking,
    SlideChunking,
    TableAwareChunking,
    dedupe_by_hash,
    make_piece,
)
from src.knowledge.chunking.prose import _SENTENCE_END
from src.knowledge.parsing.base import ContentBlock, DocumentFormat, ParsedDocument
from src.knowledge.rules import select_chunking_strategy

_PARAMS = ChunkParams(token_target=20, token_overlap=8, table_rows=3)


def _doc(
    blocks: tuple[ContentBlock, ...],
    fmt: DocumentFormat,
    *,
    table_ratio: float = 0.0,
    page_count: int = 1,
) -> ParsedDocument:
    return ParsedDocument(blocks=blocks, page_count=page_count, format=fmt, table_ratio=table_ratio)


def _prose_doc(sentences: int) -> ParsedDocument:
    text = " ".join(f"Sentence number {i} contains some words." for i in range(1, sentences + 1))
    return _doc((ContentBlock(text=text, ref={"page": 1}),), DocumentFormat.PDF)


# --- selection rule (branch-complete, patterns §4) ---


def test_select_xlsx_is_table_aware() -> None:
    strategy = select_chunking_strategy(_doc((), DocumentFormat.XLSX, table_ratio=1.0), _PARAMS)
    assert isinstance(strategy, TableAwareChunking)


def test_select_table_dominant_pdf_is_table_aware() -> None:
    strategy = select_chunking_strategy(_doc((), DocumentFormat.PDF, table_ratio=0.7), _PARAMS)
    assert isinstance(strategy, TableAwareChunking)


def test_select_ratio_at_threshold_is_not_table() -> None:
    # The threshold is strict (> 0.6): exactly 0.6 stays prose.
    strategy = select_chunking_strategy(_doc((), DocumentFormat.PDF, table_ratio=0.6), _PARAMS)
    assert isinstance(strategy, ProseChunking)


def test_select_pptx_is_slide() -> None:
    strategy = select_chunking_strategy(_doc((), DocumentFormat.PPTX), _PARAMS)
    assert isinstance(strategy, SlideChunking)


@pytest.mark.parametrize("fmt", [DocumentFormat.PDF, DocumentFormat.DOCX, DocumentFormat.TXT])
def test_select_prose_default(fmt: DocumentFormat) -> None:
    assert isinstance(select_chunking_strategy(_doc((), fmt), _PARAMS), ProseChunking)


# --- prose properties ---


def test_prose_no_chunk_exceeds_token_cap() -> None:
    chunks = ProseChunking(_PARAMS).chunk(_prose_doc(40))
    assert len(chunks) > 1
    assert all(chunk.token_count <= _PARAMS.token_target for chunk in chunks)


def test_prose_consecutive_chunks_overlap() -> None:
    chunks = ProseChunking(_PARAMS).chunk(_prose_doc(40))
    for earlier, later in zip(chunks, chunks[1:], strict=False):
        prev_tokens = earlier.content.split()
        next_tokens = later.content.split()
        # The next chunk begins with a non-empty suffix of the previous chunk.
        overlap = next(
            (k for k in range(_PARAMS.token_overlap, 0, -1) if next_tokens[:k] == prev_tokens[-k:]),
            0,
        )
        assert overlap > 0


def test_prose_respects_sentence_boundaries() -> None:
    doc = _prose_doc(40)
    originals = {s for s in _SENTENCE_END.split(doc.blocks[0].text) if s}
    for chunk in ProseChunking(_PARAMS).chunk(doc):
        for sentence in _SENTENCE_END.split(chunk.content):
            assert sentence in originals  # whole sentences only — never split mid-sentence


def test_prose_overlong_sentence_is_split_to_respect_cap() -> None:
    long_sentence = "word " * 100  # one 100-token "sentence", no terminators
    chunks = ProseChunking(_PARAMS).chunk(
        _doc((ContentBlock(text=long_sentence, ref={"page": 1}),), DocumentFormat.TXT)
    )
    assert chunks  # it does not hang or drop content
    assert all(chunk.token_count <= _PARAMS.token_target for chunk in chunks)


# --- table properties ---


def test_table_chunks_repeat_header_and_group_rows() -> None:
    header = "Region | Revenue"
    rows = "\n".join(f"R{i} | {i * 10}" for i in range(10))
    block = ContentBlock(text=f"{header}\n{rows}", ref={"sheet": "Q1"}, is_table=True)
    chunks = TableAwareChunking(_PARAMS).chunk(_doc((block,), DocumentFormat.XLSX, table_ratio=1.0))
    assert len(chunks) == 4  # ceil(10 rows / 3 per chunk)
    assert all(chunk.content.startswith(header) for chunk in chunks)
    assert all(chunk.metadata == {"sheet": "Q1"} for chunk in chunks)


def test_table_header_only_yields_one_chunk() -> None:
    block = ContentBlock(text="Region | Revenue", ref={"sheet": "Empty"}, is_table=True)
    chunks = TableAwareChunking(_PARAMS).chunk(_doc((block,), DocumentFormat.XLSX, table_ratio=1.0))
    assert len(chunks) == 1
    assert chunks[0].content == "Region | Revenue"


# --- slide properties ---


def test_slide_combines_body_and_notes_per_slide() -> None:
    blocks = (
        ContentBlock(text="Slide one body", ref={"slide": 1}),
        ContentBlock(text="Notes for one", ref={"slide": 1, "kind": "notes"}),
        ContentBlock(text="Slide two body", ref={"slide": 2}),
    )
    chunks = SlideChunking(_PARAMS).chunk(_doc(blocks, DocumentFormat.PPTX, page_count=2))
    assert len(chunks) == 2
    assert "Slide one body" in chunks[0].content and "Notes for one" in chunks[0].content
    assert chunks[0].metadata == {"slide": 1}


# --- determinism & edges ---


def test_chunking_is_deterministic() -> None:
    doc = _prose_doc(40)
    first = [chunk.content_hash for chunk in ProseChunking(_PARAMS).chunk(doc)]
    second = [chunk.content_hash for chunk in ProseChunking(_PARAMS).chunk(doc)]
    assert first == second and len(first) > 1


@pytest.mark.parametrize("fmt", [DocumentFormat.PDF, DocumentFormat.XLSX, DocumentFormat.PPTX])
def test_empty_document_yields_zero_chunks(fmt: DocumentFormat) -> None:
    strategy = select_chunking_strategy(_doc((), fmt), _PARAMS)
    assert strategy.chunk(_doc((), fmt)) == []


def test_dedupe_drops_repeated_hashes() -> None:
    pieces = [make_piece("same text", {"page": 1}), make_piece("same   text", {"page": 2})]
    assert len(dedupe_by_hash(pieces)) == 1  # whitespace-normalized hash collapses the duplicate
