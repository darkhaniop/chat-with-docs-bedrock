"""`ingest-page` render step: the two per-page raster outputs (docs/03-ingestion.md#2a-render).

| Render | Purpose | Spec |
| --- | --- | --- |
| `{page:04d}.png` | Viewer fallback, debugging | ~200 DPI, long edge capped at 3000 px |
| `{page:04d}.embed.jpg` | Nova embedding, Textract OCR | JPEG q85, long edge capped at 1568 px |

Both renders are produced with a single uniform scale factor applied to both axes via
`fitz.Matrix(scale, scale)` — computing width's and height's pixel sizes from *independently*
rounded scales (e.g. deriving one from a pre-rounded pixel width) drifts by a pixel often enough
to break the exact agreement `common.geometry.embed_render_pixel_size`'s prediction needs to hold
for the Textract bbox conversion.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import fitz  # PyMuPDF
from PIL import Image, ImageOps


@dataclass(frozen=True)
class PageRenders:
    display_png: bytes
    embed_jpg: bytes


def render_pdf_page(
    page: fitz.Page,
    *,
    display_dpi: int,
    display_max_long_edge_px: int,
    embed_max_long_edge_px: int,
) -> PageRenders:
    """`page` must already be the post-rotation page (`fitz.open(...)[i]`) — PyMuPDF's
    `get_pixmap` respects `page.rotation` automatically (confirmed live: `page.rect` and
    `get_pixmap()` both reflect `/Rotate`, unlike `get_text()` — see `common/geometry.py`'s
    module docstring), so no separate rotation step is needed here.
    """
    page_width_pt, page_height_pt = page.rect.width, page.rect.height
    long_edge_pt = max(page_width_pt, page_height_pt)

    display_scale = display_dpi / 72.0
    if long_edge_pt * display_scale > display_max_long_edge_px:
        display_scale = display_max_long_edge_px / long_edge_pt
    display_pixmap = page.get_pixmap(matrix=fitz.Matrix(display_scale, display_scale))
    display_png: bytes = display_pixmap.tobytes("png")

    # Unlike the display render, embed always scales to exactly hit the cap — there is no DPI
    # target to prefer, and no "native resolution" ceiling for vector PDF content (see
    # `embed_render_pixel_size`'s docstring).
    embed_scale = embed_max_long_edge_px / long_edge_pt
    embed_pixmap = page.get_pixmap(matrix=fitz.Matrix(embed_scale, embed_scale))
    embed_jpg: bytes = embed_pixmap.tobytes("jpeg", jpg_quality=85)

    return PageRenders(display_png=display_png, embed_jpg=embed_jpg)


def _resize_to_long_edge(image: Image.Image, max_long_edge_px: int) -> Image.Image:
    width, height = image.size
    long_edge = max(width, height)
    if long_edge <= max_long_edge_px:
        return image
    scale = max_long_edge_px / long_edge
    new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return image.resize(new_size, Image.Resampling.LANCZOS)


def render_image_document(
    data: bytes, *, display_max_long_edge_px: int, embed_max_long_edge_px: int
) -> PageRenders:
    """docs/03-ingestion.md#2a-render: "For image documents the 'render' is a re-encode: strip
    EXIF, apply EXIF rotation, convert to sRGB, and produce the same two outputs." `probe.py`'s
    `probe_image` applies the same `exif_transpose` before reading dimensions, so the Page
    item's `width`/`height` always match what comes out of here.
    """
    with Image.open(io.BytesIO(data)) as raw_image:
        image = ImageOps.exif_transpose(raw_image).convert("RGB")

    display_image = _resize_to_long_edge(image, display_max_long_edge_px)
    display_buf = io.BytesIO()
    display_image.save(display_buf, format="PNG")

    embed_image = _resize_to_long_edge(image, embed_max_long_edge_px)
    embed_buf = io.BytesIO()
    embed_image.save(embed_buf, format="JPEG", quality=85)

    return PageRenders(display_png=display_buf.getvalue(), embed_jpg=embed_buf.getvalue())
