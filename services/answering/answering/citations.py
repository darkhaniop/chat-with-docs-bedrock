"""Citation mapping: a Bedrock `content_block_location` -> `{documentId, pageNumber, rects, ...}`,
with the defensive rules that keep a bad mapping from becoming a silently wrong highlight."""

from __future__ import annotations

from common.bedrock.messages import Citation
from common.models import Chunk, CitationRecord

# docs/04 #8's defensive rules: "A citation whose `cited_text` shares no 8-character substring
# with the joined sentence text -> keep it but flag `suspect: true`."
_SUSPECT_MIN_SHARED_SUBSTRING = 8


def _shares_substring(a: str, b: str, *, length: int) -> bool:
    if not a or not b:
        return False
    if len(a) < length or len(b) < length:
        return a in b or b in a
    return any(a[i : i + length] in b for i in range(len(a) - length + 1))


def map_citation(
    citation: Citation,
    *,
    citation_id: str,
    index_to_chunk: dict[int, str],
    chunks_by_id: dict[str, Chunk],
    span_start: int,
    span_end: int,
) -> CitationRecord | None:
    """Returns `None` when the citation is dropped - an out-of-range `document_index` is never
    guessed at. The caller is expected to log a WARN and count a metric when this returns
    `None`. This function stays pure so it's trivially unit-testable."""
    chunk_id = index_to_chunk.get(citation.document_index)
    if chunk_id is None:
        return None
    chunk = chunks_by_id.get(chunk_id)
    if chunk is None:
        return None

    sentence_count = len(chunk.sentences)
    start, end = citation.start_block_index, citation.end_block_index
    if end <= start or start < 0 or end > sentence_count:
        # "clamp to the chunk's sentence count; if that yields an empty range, drop it."
        start = max(0, min(start, sentence_count))
        end = max(0, min(end, sentence_count))
        if end <= start:
            return None

    sentences = chunk.sentences[start:end]
    joined = " ".join(s.text for s in sentences)
    suspect = not _shares_substring(
        citation.cited_text, joined, length=_SUSPECT_MIN_SHARED_SUBSTRING
    )

    return CitationRecord(
        citation_id=citation_id,
        document_id=chunk.document_id,
        page_number=chunk.page_number,
        chunk_id=chunk.chunk_id,
        start_sentence=start,
        end_sentence=end,
        cited_text=citation.cited_text,
        rects=[rect for s in sentences for rect in s.rects],
        span_start=span_start,
        span_end=span_end,
        suspect=suspect,
    )
