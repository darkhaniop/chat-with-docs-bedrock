"""`ingest-probe`: magic-byte validation, page classification, limits
(docs/03-ingestion.md#step-1-probe).

Deliberately pure — no S3/DynamoDB I/O here, so the text-density threshold (the constant most
likely to need tuning against the sample corpus, per the docs) can be tested against the fixture
corpus without touching AWS. `services/ingestion/ingestion/handlers.py` is the thin Lambda
wrapper that downloads the object, calls this module, and persists the result.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import fitz  # PyMuPDF
from PIL import Image, ImageOps

from common.config import Settings
from common.models import DocumentKind, TextSource

_MAGIC_BYTES: dict[str, bytes] = {
    "application/pdf": b"%PDF-",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
}


class ProbeError(Exception):
    """The message is written verbatim to the Document's `statusDetail`
    (docs/03-ingestion.md#failure-handling--markfailed) — keep it human-readable."""


@dataclass(frozen=True)
class ProbedPage:
    page_number: int
    width: float
    height: float
    rotation: int
    text_source: TextSource
    text_density: float


@dataclass(frozen=True)
class ProbeResult:
    kind: DocumentKind
    page_count: int
    ocr_page_count: int
    pages: list[ProbedPage]


def validate_magic_bytes(content_type: str, data: bytes) -> None:
    """docs/03-ingestion.md#step-1-probe item 2: verify the declared content type against magic
    bytes rather than trusting the client's `Content-Type` header. WebP's signature is a RIFF
    container with a `WEBP` fourCC at byte 8, not a fixed byte prefix like the other three."""
    if content_type == "image/webp":
        if data[0:4] != b"RIFF" or data[8:12] != b"WEBP":
            raise ProbeError(f"Declared content type {content_type!r} does not match the file.")
        return
    expected = _MAGIC_BYTES.get(content_type)
    if expected is None:
        raise ProbeError(f"Unsupported content type {content_type!r}.")
    if not data.startswith(expected):
        raise ProbeError(f"Declared content type {content_type!r} does not match the file.")


def text_density(
    char_count: int, *, width_pt: float, height_pt: float, chars_per_sq_inch: int
) -> float:
    """docs/03-ingestion.md#step-1-probe item 4's formula, clamped to `[0, 1]`."""
    area_sq_inch = (width_pt / 72.0) * (height_pt / 72.0)
    density = char_count / max(1.0, area_sq_inch * chars_per_sq_inch)
    return max(0.0, min(1.0, density))


def classify_text_source(density: float, *, threshold: float) -> TextSource:
    return "pdf" if density >= threshold else "textract"


def probe_pdf(data: bytes, settings: Settings) -> ProbeResult:
    validate_magic_bytes("application/pdf", data)
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ProbeError("Corrupt or unsupported PDF file.") from exc

    try:
        if doc.is_encrypted:
            raise ProbeError("Encrypted PDF: password required.")
        if doc.page_count == 0:
            raise ProbeError("PDF has no pages.")
        if doc.page_count > settings.max_document_pages:
            raise ProbeError(
                f"Page count {doc.page_count} exceeds the {settings.max_document_pages} page limit."
            )

        pages: list[ProbedPage] = []
        ocr_count = 0
        for index in range(doc.page_count):
            try:
                page = doc[index]
                width, height = page.rect.width, page.rect.height
                rotation = page.rotation
                char_count = len(page.get_text("text"))
                density = text_density(
                    char_count,
                    width_pt=width,
                    height_pt=height,
                    chars_per_sq_inch=settings.text_density_chars_per_sq_inch,
                )
                source = classify_text_source(density, threshold=settings.text_density_threshold)
            except Exception:  # noqa: BLE001 — a single unreadable page must not fail the doc
                width, height, rotation, density, source = 612.0, 792.0, 0, 0.0, "none"
            if source != "pdf":
                ocr_count += 1
            pages.append(
                ProbedPage(
                    page_number=index + 1,
                    width=width,
                    height=height,
                    rotation=rotation,
                    text_source=source,
                    text_density=density,
                )
            )
        return ProbeResult(
            kind="pdf", page_count=doc.page_count, ocr_page_count=ocr_count, pages=pages
        )
    finally:
        doc.close()


def probe_image(data: bytes, content_type: str, settings: Settings) -> ProbeResult:
    """docs/03-ingestion.md#step-1-probe item 3: an image is a one-page document,
    `textSource = "textract"` unconditionally — a photograph has no text layer by definition.
    There is no PDF user-space unit for a standalone image, so its native pixel dimensions are
    used directly as the canonical `width`/`height` (1 px == 1 pt) — the render pipeline treats
    an image document's single page as already being its own display render, so this is the
    only coordinate space that ever needs to exist for it.

    Dimensions are read **after** `ImageOps.exif_transpose` so a phone photo shot in portrait but
    stored with a landscape raster + an EXIF rotation tag reports the same width/height here as
    `render.py`'s re-encode produces — otherwise a 90-degree EXIF tag would silently swap width
    and height between the Page item and the actual rendered image, sending every OCR bbox
    conversion off by a transposition.
    """
    validate_magic_bytes(content_type, data)
    try:
        with Image.open(io.BytesIO(data)) as raw_image:
            image = ImageOps.exif_transpose(raw_image)
            width_px, height_px = image.size
    except Exception as exc:
        raise ProbeError("Corrupt or unsupported image file.") from exc
    return ProbeResult(
        kind="image",
        page_count=1,
        ocr_page_count=1,
        pages=[
            ProbedPage(
                page_number=1,
                width=float(width_px),
                height=float(height_px),
                rotation=0,
                text_source="textract",
                text_density=0.0,
            )
        ],
    )


def probe(data: bytes, content_type: str, settings: Settings) -> ProbeResult:
    if len(data) > settings.max_document_bytes:
        raise ProbeError(
            f"The document is {len(data)} bytes; the limit is {settings.max_document_bytes}."
        )
    if content_type == "application/pdf":
        return probe_pdf(data, settings)
    if content_type in ("image/png", "image/jpeg", "image/webp"):
        return probe_image(data, content_type, settings)
    raise ProbeError(f"Unsupported content type {content_type!r}.")


def to_probe_json(result: ProbeResult) -> list[dict[str, object]]:
    """`artifacts/{p}/{d}/probe.json` — a **bare JSON array**, one object per page
    (docs/03-ingestion.md#state-machine: "The Distributed Map reads its item list from
    ...probe.json"). Step Functions' `S3JsonItemReader` requires the S3 object's top level to be
    a JSON array, so per-document fields (`kind`, `pageCount`, `contentType`, `projectId`,
    `documentId`) are *not* embedded here — they travel through the state machine's own data
    flow instead (Probe's Lambda return value), and the Distributed Map's `itemSelector` merges
    them back onto each per-page item. See `infra/lib/compute-stack.ts`.
    """
    return [
        {
            "pageNumber": p.page_number,
            "width": p.width,
            "height": p.height,
            "rotation": p.rotation,
            "textSource": p.text_source,
            "textDensity": p.text_density,
        }
        for p in result.pages
    ]
