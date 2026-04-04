"""`services/answering/retrieve.py`'s RRF fusion, page grouping, and selection caps
(docs/08-testing.md's `test_fusion.py`), against `FakeVectorIndex` and a moto-backed `Repo`.

Query/stored vectors are hand-constructed (not `FakeNova`) so each test controls exact cosine
rank order: `_vector(x, dim)` is unit-length with cosine similarity to the query vector
(`_vector(1.0, dim)`) equal to `x` — higher `x` ranks better (lower distance).
"""

from __future__ import annotations

import math
from typing import Literal

from answering.retrieve import RetrievalResult, retrieve
from common.config import get_settings
from common.models import Chunk, Sentence
from common.repo import Repo
from common.testing.vectors import FakeVectorIndex

_PROJECT_ID = "proj1"


def _dim() -> int:
    dim: int = get_settings().embed_dim
    return dim


def _vector(x: float, dim: int | None = None) -> list[float]:
    dim = dim if dim is not None else _dim()
    rest = math.sqrt(max(0.0, 1.0 - x * x))
    return [x, rest] + [0.0] * (dim - 2)


class _FixedNova:
    """Deliberately dumb stand-in for `NovaEmbeddingsProtocol`: every query embeds to the same
    fixed unit vector, so stored vectors' cosine rank against it is fully test-controlled."""

    def embed_text(
        self, text: str, *, purpose: Literal["GENERIC_INDEX", "GENERIC_RETRIEVAL"]
    ) -> list[float]:
        return _vector(1.0)

    def embed_image(
        self,
        image_bytes: bytes,
        *,
        image_format: Literal["png", "jpeg"],
        purpose: Literal["GENERIC_INDEX", "GENERIC_RETRIEVAL"],
    ) -> list[float]:
        raise NotImplementedError("retrieve.py never embeds images")


def _seed_chunk(
    repo: Repo,
    *,
    document_id: str,
    chunk_id: str,
    page_number: int,
    ordinal: int,
    token_estimate: int = 50,
) -> Chunk:
    chunk = Chunk(
        chunk_id=chunk_id,
        document_id=document_id,
        project_id=_PROJECT_ID,
        page_number=page_number,
        ordinal=ordinal,
        text=f"text for {chunk_id}",
        sentences=[Sentence(i=0, text=f"text for {chunk_id}", rects=[])],
        token_estimate=token_estimate,
        created_at="2026-01-01T00:00:00Z",
    )
    repo.batch_write_chunks([chunk])
    return chunk


def _index() -> FakeVectorIndex:
    index = FakeVectorIndex()
    index_name = get_settings().vector_index_name(_PROJECT_ID)
    index.create_index_if_missing(index_name, non_filterable_metadata_keys=["preview"])
    return index


def _put_text_vector(
    index: FakeVectorIndex,
    *,
    document_id: str,
    chunk_id: str,
    page_number: int,
    ordinal: int,
    rank_strength: float,
) -> None:
    index_name = get_settings().vector_index_name(_PROJECT_ID)
    index.put_vectors(
        index_name,
        [
            (
                f"{document_id}:{chunk_id}",
                _vector(rank_strength),
                {
                    "documentId": document_id,
                    "pageNumber": page_number,
                    "kind": "text",
                    "chunkId": chunk_id,
                    "ordinal": ordinal,
                    "preview": "…",
                },
            )
        ],
    )


def _put_page_vector(
    index: FakeVectorIndex, *, document_id: str, page_number: int, rank_strength: float
) -> None:
    index_name = get_settings().vector_index_name(_PROJECT_ID)
    index.put_vectors(
        index_name,
        [
            (
                f"{document_id}:p{page_number:04d}",
                _vector(rank_strength),
                {"documentId": document_id, "pageNumber": page_number, "kind": "page"},
            )
        ],
    )


def _retrieve(
    repo: Repo, index: FakeVectorIndex, *, pinned_document_ids: list[str] | None = None
) -> RetrievalResult:
    return retrieve(
        repo=repo,
        vector_index=index,
        nova=_FixedNova(),
        settings=get_settings(),
        project_id=_PROJECT_ID,
        query_text="what happened in Q3?",
        pinned_document_ids=pinned_document_ids,
    )


def test_empty_index_returns_an_empty_result(repo: Repo) -> None:
    result = _retrieve(repo, _index())
    assert result.chunks == []
    assert result.pages == []
    assert result.retrieved == []


def test_a_page_hit_in_both_lists_outranks_a_page_hit_in_only_one(repo: Repo) -> None:
    index = _index()
    _seed_chunk(repo, document_id="docA", chunk_id="c1", page_number=1, ordinal=0)
    _seed_chunk(repo, document_id="docB", chunk_id="c2", page_number=1, ordinal=0)
    # docA: ranks well in both the text and page lists. docB: ranks well in text only.
    _put_text_vector(
        index, document_id="docA", chunk_id="c1", page_number=1, ordinal=0, rank_strength=0.9
    )
    _put_page_vector(index, document_id="docA", page_number=1, rank_strength=0.9)
    _put_text_vector(
        index, document_id="docB", chunk_id="c2", page_number=1, ordinal=0, rank_strength=0.95
    )

    result = _retrieve(repo, index)

    assert result.pages[0].document_id == "docA"


def test_page_selection_caps_at_max_selected_pages(repo: Repo) -> None:
    index = _index()
    settings = get_settings().model_copy(update={"max_selected_pages": 2})
    for i in range(4):
        document_id = f"doc{i}"
        _seed_chunk(repo, document_id=document_id, chunk_id="c1", page_number=1, ordinal=0)
        _put_text_vector(
            index,
            document_id=document_id,
            chunk_id="c1",
            page_number=1,
            ordinal=0,
            rank_strength=0.9 - i * 0.1,
        )

    result = retrieve(
        repo=repo,
        vector_index=index,
        nova=_FixedNova(),
        settings=settings,
        project_id=_PROJECT_ID,
        query_text="q",
    )
    assert len(result.pages) == 2
    assert [p.document_id for p in result.pages] == ["doc0", "doc1"]


def test_chunk_selection_prefers_best_ranked_hits_up_to_the_per_page_cap(repo: Repo) -> None:
    index = _index()
    settings = get_settings().model_copy(update={"max_chunks_per_page": 1})
    _seed_chunk(repo, document_id="docA", chunk_id="best", page_number=1, ordinal=0)
    _seed_chunk(repo, document_id="docA", chunk_id="worst", page_number=1, ordinal=1)
    _put_text_vector(
        index, document_id="docA", chunk_id="worst", page_number=1, ordinal=1, rank_strength=0.5
    )
    _put_text_vector(
        index, document_id="docA", chunk_id="best", page_number=1, ordinal=0, rank_strength=0.9
    )

    result = retrieve(
        repo=repo,
        vector_index=index,
        nova=_FixedNova(),
        settings=settings,
        project_id=_PROJECT_ID,
        query_text="q",
    )
    assert [c.chunk_id for c in result.chunks] == ["best"]


def test_page_with_no_text_hit_falls_back_to_its_highest_ordinal_chunk(repo: Repo) -> None:
    index = _index()
    # Only a page-kind hit for docA — no text hit ranked it, but the page still has chunks in
    # DynamoDB (they just didn't rank in the top-topK text hits).
    _seed_chunk(repo, document_id="docA", chunk_id="c1", page_number=1, ordinal=0)
    _seed_chunk(repo, document_id="docA", chunk_id="c2", page_number=1, ordinal=1)
    _put_page_vector(index, document_id="docA", page_number=1, rank_strength=0.9)

    result = _retrieve(repo, index)

    assert result.pages[0].has_citable_text is True
    assert [c.chunk_id for c in result.chunks] == ["c2"]  # highest ordinal


def test_page_with_no_chunks_at_all_has_no_citable_text(repo: Repo) -> None:
    index = _index()
    _put_page_vector(index, document_id="docA", page_number=1, rank_strength=0.9)

    result = _retrieve(repo, index)

    assert result.pages[0].has_citable_text is False
    assert result.chunks == []


def test_total_chunk_cap_binds_across_pages(repo: Repo) -> None:
    index = _index()
    settings = get_settings().model_copy(update={"max_total_chunks": 2, "max_chunks_per_page": 2})
    for page in range(1, 4):
        document_id = "docA"
        chunk_id = f"c{page}"
        _seed_chunk(repo, document_id=document_id, chunk_id=chunk_id, page_number=page, ordinal=0)
        _put_text_vector(
            index,
            document_id=document_id,
            chunk_id=chunk_id,
            page_number=page,
            ordinal=0,
            rank_strength=0.9 - page * 0.05,
        )

    result = retrieve(
        repo=repo,
        vector_index=index,
        nova=_FixedNova(),
        settings=settings,
        project_id=_PROJECT_ID,
        query_text="q",
    )
    assert len(result.chunks) == 2


def test_context_token_cap_binds_before_the_chunk_count_cap(repo: Repo) -> None:
    index = _index()
    settings = get_settings().model_copy(update={"max_context_tokens": 100})
    _seed_chunk(
        repo, document_id="docA", chunk_id="c1", page_number=1, ordinal=0, token_estimate=80
    )
    _seed_chunk(
        repo, document_id="docA", chunk_id="c2", page_number=2, ordinal=0, token_estimate=80
    )
    _put_text_vector(
        index, document_id="docA", chunk_id="c1", page_number=1, ordinal=0, rank_strength=0.9
    )
    _put_text_vector(
        index, document_id="docA", chunk_id="c2", page_number=2, ordinal=0, rank_strength=0.85
    )

    result = retrieve(
        repo=repo,
        vector_index=index,
        nova=_FixedNova(),
        settings=settings,
        project_id=_PROJECT_ID,
        query_text="q",
    )
    assert len(result.chunks) == 1


def test_selected_chunks_are_sorted_in_reading_order(repo: Repo) -> None:
    index = _index()
    _seed_chunk(repo, document_id="docB", chunk_id="b1", page_number=1, ordinal=0)
    _seed_chunk(repo, document_id="docA", chunk_id="a2", page_number=2, ordinal=0)
    _seed_chunk(repo, document_id="docA", chunk_id="a1", page_number=1, ordinal=0)
    # Rank order (by fused score) deliberately does not match reading order.
    _put_text_vector(
        index, document_id="docB", chunk_id="b1", page_number=1, ordinal=0, rank_strength=0.95
    )
    _put_text_vector(
        index, document_id="docA", chunk_id="a2", page_number=2, ordinal=0, rank_strength=0.9
    )
    _put_text_vector(
        index, document_id="docA", chunk_id="a1", page_number=1, ordinal=0, rank_strength=0.85
    )

    result = _retrieve(repo, index)

    assert [c.chunk_id for c in result.chunks] == ["a1", "a2", "b1"]


def test_pinned_document_ids_scopes_the_search(repo: Repo) -> None:
    index = _index()
    _seed_chunk(repo, document_id="docA", chunk_id="a1", page_number=1, ordinal=0)
    _seed_chunk(repo, document_id="docB", chunk_id="b1", page_number=1, ordinal=0)
    _put_text_vector(
        index, document_id="docA", chunk_id="a1", page_number=1, ordinal=0, rank_strength=0.9
    )
    _put_text_vector(
        index, document_id="docB", chunk_id="b1", page_number=1, ordinal=0, rank_strength=0.95
    )

    result = _retrieve(repo, index, pinned_document_ids=["docA"])

    assert [c.chunk_id for c in result.chunks] == ["a1"]


def test_retrieved_carries_every_hit_before_selection(repo: Repo) -> None:
    index = _index()
    settings = get_settings().model_copy(update={"max_selected_pages": 1})
    _seed_chunk(repo, document_id="docA", chunk_id="a1", page_number=1, ordinal=0)
    _seed_chunk(repo, document_id="docB", chunk_id="b1", page_number=1, ordinal=0)
    _put_text_vector(
        index, document_id="docA", chunk_id="a1", page_number=1, ordinal=0, rank_strength=0.95
    )
    _put_text_vector(
        index, document_id="docB", chunk_id="b1", page_number=1, ordinal=0, rank_strength=0.5
    )

    result = retrieve(
        repo=repo,
        vector_index=index,
        nova=_FixedNova(),
        settings=settings,
        project_id=_PROJECT_ID,
        query_text="q",
    )
    # Only docA is selected (max_selected_pages=1), but both hits are still in `retrieved`.
    assert len(result.pages) == 1
    assert {h.chunk_id for h in result.retrieved} == {"a1", "b1"}
