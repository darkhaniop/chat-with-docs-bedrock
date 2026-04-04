"""`EmbedAndIndex`: key derivation, retry, and bounded-concurrency batching for embedding chunks
and page renders with Nova (docs/03-ingestion.md#step-5--embedandindex).

Nova has no synchronous batch-embedding request shape — confirmed live in
`tests/contract/smoke_nova.py`: a `taskType` of `BATCH_EMBEDDING` (or anything else that isn't
`SINGLE_EMBEDDING`) produces the identical generic schema error a bogus taskType does, meaning
the enum genuinely has one member. "Batched" in the docs therefore means bounded-concurrency
individual `SINGLE_EMBEDDING` calls, issued via a thread pool (`embed_max_concurrency`,
`services/common/common/config.py`) since each call is a blocking `invoke_model` — not a single
request carrying many items.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from botocore.exceptions import ClientError

from common.bedrock.embeddings import NovaEmbeddingsProtocol
from common.models import Chunk, Page
from common.vectors import chunk_vector_key, page_vector_key

# Same retry policy as `ingestion.ocr.detect_lines_with_retry` — only throttling is transient.
_RETRYABLE_CODES = {"ThrottlingException"}
_DEFAULT_MAX_ATTEMPTS = 4
_DEFAULT_BASE_DELAY_SECONDS = 2.0

Vector = tuple[str, list[float], dict[str, Any]]


def _with_retry(
    call: Callable[[], list[float]],
    *,
    max_attempts: int,
    base_delay_seconds: float,
    sleep: Callable[[float], None],
) -> list[float]:
    """Retries only `ThrottlingException` — any other `ClientError` (a real request problem, an
    auth failure) is not transient and should fail the item immediately rather than burn four
    attempts on something retrying can't fix."""
    attempt = 0
    while True:
        try:
            return call()
        except ClientError as exc:
            attempt += 1
            code = exc.response.get("Error", {}).get("Code")
            if code not in _RETRYABLE_CODES or attempt >= max_attempts:
                raise
            delay = base_delay_seconds * (2 ** (attempt - 1))
            sleep(delay + random.uniform(0, delay * 0.25))  # noqa: S311 — jitter, not security


def embed_text_with_retry(
    nova: NovaEmbeddingsProtocol,
    text: str,
    *,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    base_delay_seconds: float = _DEFAULT_BASE_DELAY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> list[float]:
    return _with_retry(
        lambda: nova.embed_text(text, purpose="GENERIC_INDEX"),
        max_attempts=max_attempts,
        base_delay_seconds=base_delay_seconds,
        sleep=sleep,
    )


def embed_image_with_retry(
    nova: NovaEmbeddingsProtocol,
    image_bytes: bytes,
    *,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    base_delay_seconds: float = _DEFAULT_BASE_DELAY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> list[float]:
    return _with_retry(
        lambda: nova.embed_image(image_bytes, image_format="jpeg", purpose="GENERIC_INDEX"),
        max_attempts=max_attempts,
        base_delay_seconds=base_delay_seconds,
        sleep=sleep,
    )


def embed_chunks(
    nova: NovaEmbeddingsProtocol, chunks: list[Chunk], *, max_concurrency: int
) -> list[Vector]:
    """One `kind: "text"` vector per chunk (docs/03-ingestion.md#step-5--embedandindex)."""

    def _job(chunk: Chunk) -> Vector:
        vector = embed_text_with_retry(nova, chunk.text)
        metadata: dict[str, Any] = {
            "documentId": chunk.document_id,
            "pageNumber": chunk.page_number,
            "kind": "text",
            "chunkId": chunk.chunk_id,
            "ordinal": chunk.ordinal,
            "preview": chunk.text[:200],
        }
        return chunk_vector_key(chunk.document_id, chunk.chunk_id), vector, metadata

    if not chunks:
        return []
    with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        return list(pool.map(_job, chunks))


def embed_pages(
    nova: NovaEmbeddingsProtocol, pages: list[tuple[Page, bytes]], *, max_concurrency: int
) -> list[Vector]:
    """One `kind: "page"` vector per page, embedded from its `.embed.jpg` render — every page
    gets one regardless of `textSource` (docs/03-ingestion.md#step-5--embedandindex: page
    vectors "give recall on figures, charts, layout-heavy pages, and scans whose OCR is poor")."""

    def _job(item: tuple[Page, bytes]) -> Vector:
        page, image_bytes = item
        vector = embed_image_with_retry(nova, image_bytes)
        metadata: dict[str, Any] = {
            "documentId": page.document_id,
            "pageNumber": page.page_number,
            "kind": "page",
            "preview": f"page {page.page_number}",
        }
        return page_vector_key(page.document_id, page.page_number), vector, metadata

    if not pages:
        return []
    with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        return list(pool.map(_job, pages))
