"""Dual page rendering (docs/03-ingestion.md#2a-render)."""

from __future__ import annotations

import io
from pathlib import Path

import fitz
from PIL import Image

from ingestion.render import render_image_document, render_pdf_page

_FIXTURES = Path(__file__).parents[3] / "e2e" / "fixtures"


def _first_page(name: str) -> fitz.Page:
    doc = fitz.open(_FIXTURES / name)
    return doc[0]


def test_render_pdf_page_display_png_is_close_to_the_target_dpi() -> None:
    page = _first_page("born-digital.pdf")  # 612x792 pt = 8.5x11 in
    renders = render_pdf_page(
        page, display_dpi=200, display_max_long_edge_px=3000, embed_max_long_edge_px=1568
    )
    with Image.open(io.BytesIO(renders.display_png)) as image:
        assert image.format == "PNG"
        # 11in * 200dpi = 2200px long edge, well under the 3000px cap.
        assert image.size == (1700, 2200)


def test_render_pdf_page_display_png_is_capped_at_the_long_edge_limit() -> None:
    page = _first_page("slide-export.pdf")  # 960x540 pt = 13.33x7.5 in
    renders = render_pdf_page(
        page, display_dpi=200, display_max_long_edge_px=1000, embed_max_long_edge_px=1568
    )
    with Image.open(io.BytesIO(renders.display_png)) as image:
        assert max(image.size) == 1000


def test_render_pdf_page_embed_jpg_is_capped_at_1568px_long_edge() -> None:
    page = _first_page("born-digital.pdf")
    renders = render_pdf_page(
        page, display_dpi=200, display_max_long_edge_px=3000, embed_max_long_edge_px=1568
    )
    with Image.open(io.BytesIO(renders.embed_jpg)) as image:
        assert image.format == "JPEG"
        assert max(image.size) == 1568
        assert image.size == (1212, 1568)  # aspect-preserved from 612x792


def test_render_pdf_page_embed_dimensions_match_geometry_prediction() -> None:
    from common.geometry import embed_render_pixel_size

    page = _first_page("two-column.pdf")
    renders = render_pdf_page(
        page, display_dpi=200, display_max_long_edge_px=3000, embed_max_long_edge_px=1568
    )
    predicted = embed_render_pixel_size(
        page_width_pt=page.rect.width, page_height_pt=page.rect.height, max_long_edge_px=1568
    )
    with Image.open(io.BytesIO(renders.embed_jpg)) as image:
        assert image.size == predicted


def test_render_image_document_produces_png_display_and_jpeg_embed() -> None:
    data = (_FIXTURES / "photograph.jpg").read_bytes()
    renders = render_image_document(
        data, display_max_long_edge_px=3000, embed_max_long_edge_px=1568
    )

    with Image.open(io.BytesIO(renders.display_png)) as display:
        assert display.format == "PNG"
        assert display.size == (1200, 900)  # under the cap: unchanged

    with Image.open(io.BytesIO(renders.embed_jpg)) as embed:
        assert embed.format == "JPEG"
        assert max(embed.size) <= 1568


def test_render_image_document_caps_a_large_image_to_the_long_edge() -> None:
    large = Image.new("RGB", (4000, 2000), "white")
    buf = io.BytesIO()
    large.save(buf, format="PNG")

    renders = render_image_document(
        buf.getvalue(), display_max_long_edge_px=3000, embed_max_long_edge_px=1568
    )

    with Image.open(io.BytesIO(renders.display_png)) as display:
        assert max(display.size) == 3000
    with Image.open(io.BytesIO(renders.embed_jpg)) as embed:
        assert max(embed.size) == 1568


def test_render_image_document_applies_exif_rotation_and_strips_it() -> None:
    image = Image.new("RGB", (300, 200), "white")
    exif = image.getexif()
    exif[0x0112] = 6  # rotate 90 CW to display upright
    buf = io.BytesIO()
    image.save(buf, format="jpeg", exif=exif.tobytes())

    renders = render_image_document(
        buf.getvalue(), display_max_long_edge_px=3000, embed_max_long_edge_px=1568
    )

    with Image.open(io.BytesIO(renders.display_png)) as display:
        assert display.size == (200, 300)
        assert display.getexif() == {}
