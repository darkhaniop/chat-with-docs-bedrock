"""`answering.citations.map_citation` (docs/04-retrieval-and-citations.md #8): the defensive
rules that keep a bad mapping from becoming a silently wrong highlight — dropped out-of-range
`document_index`, clamped sentence ranges, and `suspect` detection.
"""

from __future__ import annotations

from answering.citations import map_citation
from common.bedrock.messages import Citation
from common.models import Chunk, Sentence


def _chunk(sentences: list[str]) -> Chunk:
    return Chunk(
        chunk_id="chunk-1",
        document_id="doc-1",
        project_id="proj-1",
        page_number=7,
        ordinal=0,
        text=" ".join(sentences),
        sentences=[
            Sentence(i=i, text=text, rects=[(float(i), 0.0, float(i) + 1.0, 1.0)])
            for i, text in enumerate(sentences)
        ],
        token_estimate=50,
        created_at="2026-01-01T00:00:00Z",
    )


def _citation(**overrides: object) -> Citation:
    defaults: dict[str, object] = {
        "cited_text": "The facility achieved 94% uptime in Q3.",
        "document_index": 0,
        "document_title": "report.pdf — page 7",
        "start_block_index": 0,
        "end_block_index": 1,
    }
    defaults.update(overrides)
    return Citation(**defaults)  # type: ignore[arg-type]


def test_maps_a_well_formed_citation_end_to_end() -> None:
    chunk = _chunk(["The facility achieved 94% uptime in Q3.", "This was attributable to cooling."])
    result = map_citation(
        _citation(),
        citation_id="c0",
        index_to_chunk={0: chunk.chunk_id},
        chunks_by_id={chunk.chunk_id: chunk},
        span_start=10,
        span_end=50,
    )

    assert result is not None
    assert result.citation_id == "c0"
    assert result.document_id == "doc-1"
    assert result.page_number == 7
    assert result.chunk_id == "chunk-1"
    assert result.start_sentence == 0
    assert result.end_sentence == 1
    assert result.rects == [(0.0, 0.0, 1.0, 1.0)]
    assert result.span_start == 10
    assert result.span_end == 50
    assert result.suspect is False


def test_drops_a_citation_with_an_out_of_range_document_index() -> None:
    chunk = _chunk(["one", "two"])
    result = map_citation(
        _citation(document_index=5),
        citation_id="c0",
        index_to_chunk={0: chunk.chunk_id},
        chunks_by_id={chunk.chunk_id: chunk},
        span_start=0,
        span_end=10,
    )
    assert result is None


def test_clamps_an_end_index_past_the_sentence_count() -> None:
    chunk = _chunk(["one", "two"])
    result = map_citation(
        _citation(cited_text="two", start_block_index=1, end_block_index=99),
        citation_id="c0",
        index_to_chunk={0: chunk.chunk_id},
        chunks_by_id={chunk.chunk_id: chunk},
        span_start=0,
        span_end=3,
    )
    assert result is not None
    assert result.start_sentence == 1
    assert result.end_sentence == 2
    assert result.cited_text == "two"


def test_drops_a_citation_whose_clamped_range_is_empty() -> None:
    chunk = _chunk(["one", "two"])
    result = map_citation(
        _citation(start_block_index=5, end_block_index=9),
        citation_id="c0",
        index_to_chunk={0: chunk.chunk_id},
        chunks_by_id={chunk.chunk_id: chunk},
        span_start=0,
        span_end=3,
    )
    assert result is None


def test_drops_a_citation_with_end_index_not_after_start_index() -> None:
    chunk = _chunk(["one", "two", "three"])
    result = map_citation(
        _citation(start_block_index=1, end_block_index=1),
        citation_id="c0",
        index_to_chunk={0: chunk.chunk_id},
        chunks_by_id={chunk.chunk_id: chunk},
        span_start=0,
        span_end=3,
    )
    assert result is None


def test_flags_suspect_when_cited_text_shares_no_long_substring_with_the_sentences() -> None:
    chunk = _chunk(["The facility achieved 94% uptime in Q3."])
    result = map_citation(
        _citation(cited_text="Completely unrelated made-up quotation text here."),
        citation_id="c0",
        index_to_chunk={0: chunk.chunk_id},
        chunks_by_id={chunk.chunk_id: chunk},
        span_start=0,
        span_end=10,
    )
    assert result is not None
    assert result.suspect is True


def test_not_suspect_when_cited_text_is_a_substring_of_the_sentence() -> None:
    chunk = _chunk(["The facility achieved 94% uptime in Q3, a new record."])
    result = map_citation(
        _citation(cited_text="94% uptime in Q3"),
        citation_id="c0",
        index_to_chunk={0: chunk.chunk_id},
        chunks_by_id={chunk.chunk_id: chunk},
        span_start=0,
        span_end=10,
    )
    assert result is not None
    assert result.suspect is False
