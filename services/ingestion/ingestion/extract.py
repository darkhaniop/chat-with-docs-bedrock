"""`ingest-page` PDF-native text extraction with geometry
(docs/03-ingestion.md#2b-extract-text-with-geometry, the `textSource == "pdf"` path).

Textract's OCR path lives in `ocr.py` — this module is PyMuPDF only.
"""

from __future__ import annotations

from dataclasses import dataclass

import fitz  # PyMuPDF

from common.geometry import apply_affine_to_rect
from common.models import Rect


@dataclass(frozen=True)
class ExtractedLine:
    text: str
    rect: Rect


def extract_pdf_lines(page: fitz.Page) -> list[ExtractedLine]:
    """Keeps lines, not blocks (docs/03-ingestion.md#2b: "a block can span a whole column, and
    line-level boxes are what make sentence highlighting look right").

    `page.get_text("dict")` line bboxes are the *raw, untransformed* content-stream
    coordinates — they do **not** account for `/Rotate` even though `page.rect` and
    `page.get_pixmap()` do (see `common/geometry.py`'s module docstring for the full finding).
    Every bbox is normalised through `page.rotation_matrix` here, at extraction time, per
    docs/03-ingestion.md#2b-extract-text-with-geometry; for an unrotated page that matrix is the
    identity, so this is a safe no-op in the common case. Empty lines (a block with only
    whitespace spans, which PyMuPDF can emit for layout blocks with no visible text) are
    dropped.
    """
    rotation_matrix = page.rotation_matrix
    matrix = (
        rotation_matrix.a,
        rotation_matrix.b,
        rotation_matrix.c,
        rotation_matrix.d,
        rotation_matrix.e,
        rotation_matrix.f,
    )
    lines: list[ExtractedLine] = []
    text_dict = page.get_text("dict")
    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"])
            if not text.strip():
                continue
            x0, y0, x1, y1 = line["bbox"]
            rect = apply_affine_to_rect((x0, y0, x1, y1), matrix)
            lines.append(ExtractedLine(text=text, rect=rect))
    return lines
