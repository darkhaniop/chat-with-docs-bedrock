"""`ingest-page` OCR step: Textract `DetectDocumentText` + geometry conversion
(docs/03-ingestion.md#2b-extract-text-with-geometry, the `textSource == "textract"` path).

`common.ocr.Textract` is the thin boto3 adapter (confirmed live against a real fixture image in
Phase 0); this module adds the retry policy the docs call for and the conversion from Textract's
`[0,1]`-normalised bbox to canonical PDF points.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from botocore.exceptions import ClientError

from common.geometry import Rect, textract_bbox_to_canonical
from common.ocr import TextLine, Textract

# docs/03-ingestion.md#state-machine: "Retry on States.TaskFailed/... with exponential backoff
# (2 s base, 2x rate, 4 attempts)" — reused here for the same throttling codes named in
# docs/03-ingestion.md#2b ("Textract calls are wrapped in retry-with-jitter on
# ThrottlingException and ProvisionedThroughputExceededException").
_RETRYABLE_CODES = {"ThrottlingException", "ProvisionedThroughputExceededException"}
_DEFAULT_MAX_ATTEMPTS = 4
_DEFAULT_BASE_DELAY_SECONDS = 2.0


@dataclass(frozen=True)
class OcrLine:
    text: str
    rect: Rect


def detect_lines_with_retry(
    textract: Textract,
    image_bytes: bytes,
    *,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    base_delay_seconds: float = _DEFAULT_BASE_DELAY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> list[TextLine]:
    """Retries only `ThrottlingException`/`ProvisionedThroughputExceededException` — any other
    `ClientError` (a real image problem, an auth failure) is not transient and should fail the
    page immediately rather than burn four attempts on something retrying can't fix."""
    attempt = 0
    while True:
        try:
            return textract.detect_lines(image_bytes)
        except ClientError as exc:
            attempt += 1
            code = exc.response.get("Error", {}).get("Code")
            if code not in _RETRYABLE_CODES or attempt >= max_attempts:
                raise
            delay = base_delay_seconds * (2 ** (attempt - 1))
            sleep(delay + random.uniform(0, delay * 0.25))  # noqa: S311 — jitter, not security


def convert_lines_to_canonical(
    lines: list[TextLine],
    *,
    embed_width_px: int,
    embed_height_px: int,
    page_width_pt: float,
    page_height_pt: float,
) -> list[OcrLine]:
    """`embed_width_px`/`embed_height_px` must be the *actual* pixel dimensions of the image
    bytes that were sent to Textract (from `render.py`'s real output, not re-derived from page
    point dimensions) — see `common.geometry.embed_render_pixel_size`'s docstring for why the
    two must never be conflated for image documents."""
    return [
        OcrLine(
            text=line.text,
            rect=textract_bbox_to_canonical(
                line.bbox,
                embed_width_px=embed_width_px,
                embed_height_px=embed_height_px,
                page_width_pt=page_width_pt,
                page_height_pt=page_height_pt,
            ),
        )
        for line in lines
    ]
