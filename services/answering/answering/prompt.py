"""Prompt assembly (docs/04-retrieval-and-citations.md #5): one custom-content `document` block
per selected chunk, one text block per sentence, page images for text-thin pages, everything in
reading order, and the `documentIndex -> chunkId` map the citation mapper resolves against.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

from answering.retrieve import SelectedPage
from common.bedrock.messages import DocumentBlock, document_block
from common.models import Chunk

SYSTEM_PROMPT = """\
You answer questions strictly from the provided documents.

- Ground every factual claim in the documents. Cite as you write.
- If the documents do not contain the answer, say so plainly and stop. Do not answer
  from general knowledge, and do not speculate.
- Page images are provided for visual context only and cannot be cited. If a fact comes
  only from an image with no corresponding text document, say that you can see it but
  cannot cite it.
- Prefer quoting numbers, names and dates exactly as they appear.
- Be direct. No preamble, no restating the question, no "based on the documents".
- When sources disagree, say so and cite both.
"""

_PAGE_IMAGE_CAPTION = (
    "The image above is page {page} of {filename}, provided for visual context. Cite the "
    "text document that follows it, not the image."
)

_NO_DOCUMENTS_NOTE = (
    "(No documents were retrieved for this project. If you have no relevant information, say"
    " so plainly rather than guessing.)"
)


@dataclass(frozen=True)
class PromptContext:
    content_blocks: list[dict[str, Any]] = field(default_factory=list)
    index_to_chunk: dict[int, str] = field(default_factory=dict)


def thin_pages(
    chunks: list[Chunk], pages: list[SelectedPage], *, thin_text_char_threshold: int
) -> list[SelectedPage]:
    """docs/04 "Page images in the prompt": a selected page is thin if its captured chunk text
    totals under the threshold char count — including a page with no captured chunk at all
    (0 characters), which is why this checks every selected page, not just chunkless ones."""
    chars_by_page: dict[tuple[str, int], int] = {}
    for chunk in chunks:
        key = (chunk.document_id, chunk.page_number)
        chars_by_page[key] = chars_by_page.get(key, 0) + len(chunk.text)
    return [
        page
        for page in pages
        if chars_by_page.get((page.document_id, page.page_number), 0) < thin_text_char_threshold
    ]


def build_prompt_content(
    *,
    chunks: list[Chunk],
    pages: list[SelectedPage],
    filenames: dict[str, str],
    page_images: dict[tuple[str, int], bytes],
) -> PromptContext:
    """`chunks` must already be in reading order (`answering.retrieve.retrieve`'s contract).
    `filenames` maps documentId -> filename, for human-readable titles/captions. `page_images`
    maps `(documentId, pageNumber) -> .embed.jpg bytes` for exactly the pages `thin_pages`
    identified — the caller fetches only those from S3, not every selected page.
    """
    if not chunks and not pages:
        return PromptContext(content_blocks=[{"type": "text", "text": _NO_DOCUMENTS_NOTE}])

    chunks_by_page: dict[tuple[str, int], list[Chunk]] = {}
    for chunk in chunks:
        chunks_by_page.setdefault((chunk.document_id, chunk.page_number), []).append(chunk)

    # docs/04 #ordering: "(documentId, pageNumber, ordinal) — reading order, not relevance
    # order." `pages` arrives in fused-score order; re-sort here for the prompt specifically.
    reading_order_pages = sorted(pages, key=lambda p: (p.document_id, p.page_number))

    content_blocks: list[dict[str, Any]] = []
    index_to_chunk: dict[int, str] = {}
    document_index = 0

    for page in reading_order_pages:
        key = (page.document_id, page.page_number)
        image_bytes = page_images.get(key)
        if image_bytes is not None:
            filename = filenames.get(page.document_id, page.document_id)
            content_blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.b64encode(image_bytes).decode("ascii"),
                    },
                }
            )
            content_blocks.append(
                {
                    "type": "text",
                    "text": _PAGE_IMAGE_CAPTION.format(page=page.page_number, filename=filename),
                }
            )
        for chunk in chunks_by_page.get(key, []):
            filename = filenames.get(chunk.document_id, chunk.document_id)
            block = document_block(
                DocumentBlock(
                    title=f"{filename} — page {chunk.page_number}",
                    context=(
                        f"documentId={chunk.document_id} chunkId={chunk.chunk_id} "
                        f"page={chunk.page_number}"
                    ),
                    sentences=[s.text for s in chunk.sentences],
                )
            )
            content_blocks.append(block)
            index_to_chunk[document_index] = chunk.chunk_id
            document_index += 1

    return PromptContext(content_blocks=content_blocks, index_to_chunk=index_to_chunk)
