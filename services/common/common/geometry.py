"""The one place canonical-coordinate arithmetic happens (docs/02-data-model.md#coordinate-systems).

Canonical space: PDF user-space points, origin top-left, y increasing downward. A page's
``width``/``height`` on the Page item are in this space, and every rect stored on a Chunk's
sentence is ``(x0, y0, x1, y1)`` in this space.

**PyMuPDF rotation finding (Phase 3, pymupdf==1.27.2.2), corrected after an earlier draft of
this docstring got it backwards:** ``page.rect`` and ``page.get_pixmap()`` *do* reflect
``/Rotate`` (a page whose ``/MediaBox`` is 612x792 but carries ``/Rotate 90`` reports
``page.rect == (0, 0, 792, 612)``, and the pixmap is rendered upright at 792x612px). But
``page.get_text("dict")`` line bboxes are **not** rotated — they are the raw, untransformed
content-stream coordinates, still in the original 612x792 space, and can report `y` values well
past the *new* page height once real (non-trivial) content fills the page. This was missed on
first pass because the original three-sentence rotated fixture was short enough to coincidentally
fit inside the post-rotation box either way; it surfaced once ``e2e/fixtures/rotated.pdf`` was
made dense enough to cross the text-density threshold (docs/10-roadmap.md's Phase 3 log) and a
geometry test caught lines with `y1` up to ~644 on a 612-tall canonical page.

So the docs' original instruction stands after all: `extract.py` must apply
``page.rotation_matrix`` to every raw line bbox at extraction time via `apply_affine_to_rect`
below, never at render time. For an unrotated page, `page.rotation_matrix` is the identity
matrix, so applying it unconditionally is a safe no-op rather than a special case.
"""

from __future__ import annotations

from common.ocr import NormalizedBoundingBox

Rect = tuple[float, float, float, float]  # (x0, y0, x1, y1), canonical points

# A 2D affine transform as PDF/PyMuPDF's `fitz.Matrix` stores it: (a, b, c, d, e, f), applied as
# x' = a*x + c*y + e,  y' = b*x + d*y + f. Kept as a plain tuple (not a `fitz.Matrix`) so this
# module has no PyMuPDF dependency — only `services/ingestion` needs PyMuPDF itself.
AffineMatrix = tuple[float, float, float, float, float, float]


def apply_affine_to_rect(rect: Rect, matrix: AffineMatrix) -> Rect:
    """Transforms both corners of `rect` and re-derives an axis-aligned bounding rect from the
    results, because a rotation can swap which transformed corner ends up top-left vs
    bottom-right."""
    a, b, c, d, e, f = matrix
    x0, y0, x1, y1 = rect
    tx0, ty0 = a * x0 + c * y0 + e, b * x0 + d * y0 + f
    tx1, ty1 = a * x1 + c * y1 + e, b * x1 + d * y1 + f
    return (min(tx0, tx1), min(ty0, ty1), max(tx0, tx1), max(ty0, ty1))


def embed_render_pixel_size(
    *, page_width_pt: float, page_height_pt: float, max_long_edge_px: int
) -> tuple[int, int]:
    """Pixel dimensions of a **PDF page's** `.embed.jpg` render (docs/03-ingestion.md#2a-render:
    long edge scaled to `max_long_edge_px`, aspect preserved). Used only by `render.py`'s
    `render_pdf_page` to pick the PyMuPDF zoom factor.

    This always scales the long edge to exactly `max_long_edge_px` — there is no "native
    resolution" to avoid exceeding, because points and pixels are different units and PDF
    rendering is vector (no upscaling artifact to worry about). This is deliberately **not**
    used for image documents: `render_image_document`'s `_resize_to_long_edge` caps a
    photograph's embed render at its native pixel size instead, and the Textract geometry
    conversion functions below take the *actual* rendered embed dimensions as explicit
    parameters rather than re-deriving them from this function, precisely so the two documents
    kinds' different scaling policies can never be conflated.
    """
    if page_width_pt <= 0 or page_height_pt <= 0:
        raise ValueError("page dimensions must be positive")
    long_edge_pt = max(page_width_pt, page_height_pt)
    scale = max_long_edge_px / long_edge_pt
    width_px = max(1, round(page_width_pt * scale))
    height_px = max(1, round(page_height_pt * scale))
    return width_px, height_px


def textract_bbox_to_canonical(
    bbox: NormalizedBoundingBox,
    *,
    embed_width_px: float,
    embed_height_px: float,
    page_width_pt: float,
    page_height_pt: float,
) -> Rect:
    """docs/02-data-model.md#coordinate-systems: multiply Textract's ``[0,1]``-normalised
    bounding box by the embed render's pixel dimensions, then scale by
    ``page.width / embedWidthPx`` (and the analogous height ratio) to land in canonical points.
    x and y are scaled independently rather than by one shared factor so that integer rounding
    of the embed render's pixel dimensions (`embed_render_pixel_size`) can never introduce a
    skew between the two axes.
    """
    scale_x = page_width_pt / embed_width_px
    scale_y = page_height_pt / embed_height_px
    x0 = bbox.left * embed_width_px * scale_x
    y0 = bbox.top * embed_height_px * scale_y
    x1 = x0 + bbox.width * embed_width_px * scale_x
    y1 = y0 + bbox.height * embed_height_px * scale_y
    return (x0, y0, x1, y1)


def canonical_to_textract_bbox(
    rect: Rect,
    *,
    embed_width_px: float,
    embed_height_px: float,
    page_width_pt: float,
    page_height_pt: float,
) -> NormalizedBoundingBox:
    """The inverse of `textract_bbox_to_canonical`, used only by the round-trip property test —
    nothing in the pipeline itself needs to go canonical -> Textract-normalised."""
    scale_x = embed_width_px / page_width_pt
    scale_y = embed_height_px / page_height_pt
    x0, y0, x1, y1 = rect
    return NormalizedBoundingBox(
        left=(x0 * scale_x) / embed_width_px,
        top=(y0 * scale_y) / embed_height_px,
        width=((x1 - x0) * scale_x) / embed_width_px,
        height=((y1 - y0) * scale_y) / embed_height_px,
    )


def clamp_rect_to_page(rect: Rect, *, page_width: float, page_height: float) -> Rect:
    """OCR and rendering error can push a bbox a hair outside the page box; clamp rather than
    let a downstream renderer draw a highlight that overhangs the page."""
    x0, y0, x1, y1 = rect
    return (
        max(0.0, min(x0, page_width)),
        max(0.0, min(y0, page_height)),
        max(0.0, min(x1, page_width)),
        max(0.0, min(y1, page_height)),
    )


def clip_rect_horizontal(rect: Rect, *, start_fraction: float, end_fraction: float) -> Rect:
    """docs/03-ingestion.md#sentence-segmentation: clip a line's rect to the horizontal extent of
    a sentence that starts or ends mid-line, proportional to the sentence's character position
    within the line's text. Approximate (proportional-width clipping ignores per-glyph width
    variation) but visually correct in the overwhelming majority of cases, and cheap — the same
    trade-off the docs call out.
    """
    if not 0.0 <= start_fraction <= end_fraction <= 1.0:
        raise ValueError(f"invalid clip fractions: {start_fraction=} {end_fraction=}")
    x0, y0, x1, y1 = rect
    width = x1 - x0
    return (x0 + width * start_fraction, y0, x0 + width * end_fraction, y1)
