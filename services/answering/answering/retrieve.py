"""Retrieval (docs/04-retrieval-and-citations.md, steps 2-4): embed the query, search the
project's S3 Vectors index, fuse text/page hits by page with Reciprocal Rank Fusion, select
pages and chunks under the documented caps, and hydrate the selected chunks from DynamoDB in
reading order. No generation here — that's Phase 5's `services/answering/prompt.py` and
`generate.py`, which consume this module's `RetrievalResult`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from common.bedrock.embeddings import NovaEmbeddingsProtocol
from common.config import Settings
from common.models import Chunk
from common.repo import Repo
from common.retry import with_retry
from common.vectors import VectorIndexProtocol, VectorMatch

PageKey = tuple[str, int]  # (documentId, pageNumber)


@dataclass(frozen=True)
class RetrievedHit:
    """One S3 Vectors match, before fusion — the shape docs/02-data-model.md's Message
    `retrieved` field persists (Phase 5) and the eval harness's Recall@10 reads (task 7)."""

    document_id: str
    page_number: int
    kind: Literal["text", "page"]
    chunk_id: str | None
    score: float | None


@dataclass(frozen=True)
class SelectedPage:
    document_id: str
    page_number: int
    fused_score: float
    has_citable_text: bool


@dataclass(frozen=True)
class RetrievalResult:
    chunks: list[Chunk]  # selected, hydrated, sorted (documentId, pageNumber, ordinal)
    pages: list[SelectedPage]  # top pages by fused score, in fused-score order
    retrieved: list[RetrievedHit]  # every hit query_vectors returned, before fusion/selection


def _rrf_scores(hits: list[VectorMatch], *, k: int) -> dict[PageKey, float]:
    """docs/04-retrieval-and-citations.md#4-fusion-and-selection: `score(page) = Σ over lists
    1 / (k + rank_in_list(page))`, computed independently per input list (`hits` here is always
    one list — text or page — never mixed)."""
    scores: dict[PageKey, float] = {}
    for rank, hit in enumerate(hits, start=1):
        page = (str(hit.metadata["documentId"]), int(hit.metadata["pageNumber"]))
        scores[page] = scores.get(page, 0.0) + 1.0 / (k + rank)
    return scores


def retrieve(
    *,
    repo: Repo,
    vector_index: VectorIndexProtocol,
    nova: NovaEmbeddingsProtocol,
    settings: Settings,
    project_id: str,
    query_text: str,
    pinned_document_ids: list[str] | None = None,
) -> RetrievalResult:
    index_name = settings.vector_index_name(project_id)
    # docs/04 #2, failure table "Embedding fails -> Retry twice, then fail the turn with a
    # retryable error": this is the one Bedrock call in the turn with no graceful fallback (a
    # missing query embedding means no search is possible at all), so it's the one that needs
    # the explicit backoff rather than degrading in place the way `rewrite_query` does.
    query_vector = with_retry(
        lambda: nova.embed_text(query_text, purpose=settings.nova_embed_purpose_query),
        max_attempts=settings.bedrock_retry_max_attempts,
        base_delay_seconds=settings.bedrock_retry_base_delay_seconds,
    )
    filter_ = {"documentId": {"$in": pinned_document_ids}} if pinned_document_ids else None
    hits = vector_index.query(
        index_name, query_vector, top_k=settings.vector_query_top_k, filter=filter_
    )

    text_hits = [h for h in hits if h.metadata.get("kind") == "text"]
    page_hits = [h for h in hits if h.metadata.get("kind") == "page"]

    fused = _rrf_scores(text_hits, k=settings.rrf_k)
    for page, score in _rrf_scores(page_hits, k=settings.rrf_k).items():
        fused[page] = fused.get(page, 0.0) + score
    ranked_pages = sorted(fused.items(), key=lambda item: item[1], reverse=True)
    top_pages = ranked_pages[: settings.max_selected_pages]

    # docs: "include its text chunks that appeared in the text-hit list, best first" — group by
    # page while preserving each hit's original (score) rank order within that page.
    text_hits_by_page: dict[PageKey, list[VectorMatch]] = {}
    for hit in text_hits:
        page = (str(hit.metadata["documentId"]), int(hit.metadata["pageNumber"]))
        text_hits_by_page.setdefault(page, []).append(hit)

    selected_pages: list[SelectedPage] = []
    selected_chunks: list[Chunk] = []
    total_tokens = 0

    for (document_id, page_number), fused_score in top_pages:
        page_text_hits = text_hits_by_page.get((document_id, page_number), [])
        page_text_hits = page_text_hits[: settings.max_chunks_per_page]
        chunk_ids = [str(hit.metadata["chunkId"]) for hit in page_text_hits]
        # `batch_get_chunks` re-orders its result to match `chunk_ids`, preserving rank order.
        page_chunks = repo.batch_get_chunks([(document_id, cid) for cid in chunk_ids])

        if not page_chunks:
            # docs: "If a selected page contributed no text chunk ..., include its highest-
            # ordinal text chunk if one exists" — the page may have chunks that simply didn't
            # rank in the top-`topK` text hits.
            fallback = repo.list_chunks_for_page(document_id, page_number)
            if fallback:
                page_chunks = [max(fallback, key=lambda c: c.ordinal)]

        selected_pages.append(
            SelectedPage(
                document_id=document_id,
                page_number=page_number,
                fused_score=fused_score,
                has_citable_text=len(page_chunks) > 0,
            )
        )

        for chunk in page_chunks:
            if len(selected_chunks) >= settings.max_total_chunks:
                break
            if total_tokens + chunk.token_estimate > settings.max_context_tokens:
                break
            selected_chunks.append(chunk)
            total_tokens += chunk.token_estimate

    # docs/04-retrieval-and-citations.md#ordering: "Context documents are ordered by
    # (documentId, pageNumber, ordinal) — reading order, not relevance order."
    selected_chunks.sort(key=lambda c: (c.document_id, c.page_number, c.ordinal))

    retrieved = [
        RetrievedHit(
            document_id=str(hit.metadata.get("documentId", "")),
            page_number=int(hit.metadata.get("pageNumber", 0)),
            kind="text" if hit.metadata.get("kind") == "text" else "page",
            chunk_id=(str(hit.metadata["chunkId"]) if "chunkId" in hit.metadata else None),
            score=hit.distance,
        )
        for hit in hits
    ]

    return RetrievalResult(chunks=selected_chunks, pages=selected_pages, retrieved=retrieved)
