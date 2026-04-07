"""`answering.prompt` (docs/04-retrieval-and-citations.md #5): thin-page detection, reading-order
interleaving of images/documents, and the `documentIndex -> chunkId` map.
"""

from __future__ import annotations

from answering.prompt import build_prompt_content, thin_pages
from answering.retrieve import SelectedPage
from common.models import Chunk, Sentence


def _chunk(document_id: str, page_number: int, ordinal: int, text: str) -> Chunk:
    return Chunk(
        chunk_id=f"{document_id}-{page_number}-{ordinal}",
        document_id=document_id,
        project_id="proj1",
        page_number=page_number,
        ordinal=ordinal,
        text=text,
        sentences=[Sentence(i=0, text=text, rects=[(0.0, 0.0, 1.0, 1.0)])],
        token_estimate=10,
        created_at="2026-01-01T00:00:00Z",
    )


def _page(document_id: str, page_number: int, *, has_citable_text: bool = True) -> SelectedPage:
    return SelectedPage(
        document_id=document_id,
        page_number=page_number,
        fused_score=1.0,
        has_citable_text=has_citable_text,
    )


def test_thin_pages_includes_a_page_with_no_chunks_at_all() -> None:
    pages = [_page("doc1", 1, has_citable_text=False)]

    result = thin_pages([], pages, thin_text_char_threshold=200)

    assert result == pages


def test_thin_pages_excludes_a_page_whose_chunk_text_clears_the_threshold() -> None:
    chunks = [_chunk("doc1", 1, 0, "x" * 250)]
    pages = [_page("doc1", 1)]

    result = thin_pages(chunks, pages, thin_text_char_threshold=200)

    assert result == []


def test_thin_pages_includes_a_page_whose_chunk_text_is_under_the_threshold() -> None:
    chunks = [_chunk("doc1", 1, 0, "short")]
    pages = [_page("doc1", 1)]

    result = thin_pages(chunks, pages, thin_text_char_threshold=200)

    assert result == pages


def test_build_prompt_content_with_no_chunks_or_pages_adds_a_note_and_no_index_map() -> None:
    ctx = build_prompt_content(chunks=[], pages=[], filenames={}, page_images={})

    assert len(ctx.content_blocks) == 1
    assert ctx.content_blocks[0]["type"] == "text"
    assert "No documents" in ctx.content_blocks[0]["text"]
    assert ctx.index_to_chunk == {}


def test_build_prompt_content_orders_by_document_and_page_not_fused_score() -> None:
    # `pages` arrives in fused-score order (page 2 ranked first); the prompt must still read
    # page 1 before page 2 within the same document.
    chunk_p1 = _chunk("doc1", 1, 0, "page one text")
    chunk_p2 = _chunk("doc1", 2, 0, "page two text")
    pages = [_page("doc1", 2), _page("doc1", 1)]

    ctx = build_prompt_content(
        chunks=[chunk_p1, chunk_p2],
        pages=pages,
        filenames={"doc1": "report.pdf"},
        page_images={},
    )

    document_blocks = [b for b in ctx.content_blocks if b["type"] == "document"]
    assert document_blocks[0]["title"] == "report.pdf — page 1"
    assert document_blocks[1]["title"] == "report.pdf — page 2"
    assert ctx.index_to_chunk == {0: chunk_p1.chunk_id, 1: chunk_p2.chunk_id}


def test_build_prompt_content_attaches_an_image_only_for_pages_with_bytes() -> None:
    chunk = _chunk("doc1", 1, 0, "some text")
    pages = [_page("doc1", 1)]

    ctx = build_prompt_content(
        chunks=[chunk],
        pages=pages,
        filenames={"doc1": "report.pdf"},
        page_images={("doc1", 1): b"fake-jpeg-bytes"},
    )

    assert ctx.content_blocks[0]["type"] == "image"
    assert ctx.content_blocks[1]["type"] == "text"
    assert "report.pdf" in ctx.content_blocks[1]["text"]
    assert ctx.content_blocks[2]["type"] == "document"
    # A document block always has citations enabled — docs/04: "must be enabled on all
    # documents or none."
    assert ctx.content_blocks[2]["citations"] == {"enabled": True}


def test_build_prompt_content_uses_the_document_id_as_a_fallback_title_when_filename_missing() -> (
    None
):
    chunk = _chunk("doc1", 1, 0, "text")
    ctx = build_prompt_content(
        chunks=[chunk], pages=[_page("doc1", 1)], filenames={}, page_images={}
    )

    document_block = ctx.content_blocks[0]
    assert document_block["title"] == "doc1 — page 1"
