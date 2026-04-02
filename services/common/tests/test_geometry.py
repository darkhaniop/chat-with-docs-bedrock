"""Coordinate-conversion unit tests (docs/08-testing.md#geometry-tests).

Hand-checked, PyMuPDF-specific fixtures (single column / two column / rotated page line
extraction) live in ``services/ingestion/tests/test_extract.py`` next to the extraction code
that produces them. This file covers the pure numeric conversions in `common/geometry.py`:
the Textract round trip, page-box clamping, and rect clipping.
"""

from __future__ import annotations

import pytest

from common.geometry import (
    canonical_to_textract_bbox,
    clamp_rect_to_page,
    clip_rect_horizontal,
    embed_render_pixel_size,
    textract_bbox_to_canonical,
)
from common.ocr import NormalizedBoundingBox

# -- embed_render_pixel_size ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("width_pt", "height_pt"),
    [(612.0, 792.0), (792.0, 612.0), (960.0, 540.0), (2000.0, 100.0), (100.0, 2000.0)],
)
def test_embed_render_pixel_size_caps_long_edge_and_preserves_aspect(
    width_pt: float, height_pt: float
) -> None:
    width_px, height_px = embed_render_pixel_size(
        page_width_pt=width_pt, page_height_pt=height_pt, max_long_edge_px=1568
    )
    assert max(width_px, height_px) <= 1568
    expected_ratio = width_pt / height_pt
    actual_ratio = width_px / height_px
    assert actual_ratio == pytest.approx(expected_ratio, rel=0.01)


def test_embed_render_pixel_size_scales_a_small_page_up_to_the_cap() -> None:
    # Points and pixels are different units — a page whose point dimensions are numerically
    # smaller than the pixel cap must still be scaled *up* to reach it (PDF rendering is
    # vector, so there's no "native resolution" ceiling the way a raster image has).
    width_px, height_px = embed_render_pixel_size(
        page_width_pt=200.0, page_height_pt=100.0, max_long_edge_px=1568
    )
    assert (width_px, height_px) == (1568, 784)


# -- Textract round trip -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("page_width_pt", "page_height_pt", "rect"),
    [
        (612.0, 792.0, (72.0, 640.2, 511.4, 655.8)),
        (612.0, 792.0, (0.0, 0.0, 612.0, 792.0)),
        (792.0, 612.0, (72.0, 82.8, 223.18, 104.78)),
        (960.0, 540.0, (10.0, 10.0, 950.0, 60.0)),
        (200.0, 100.0, (0.0, 0.0, 200.0, 100.0)),
    ],
)
def test_textract_round_trip_is_identity_within_half_a_point(
    page_width_pt: float, page_height_pt: float, rect: tuple[float, float, float, float]
) -> None:
    embed_width_px, embed_height_px = embed_render_pixel_size(
        page_width_pt=page_width_pt, page_height_pt=page_height_pt, max_long_edge_px=1568
    )
    bbox = canonical_to_textract_bbox(
        rect,
        embed_width_px=embed_width_px,
        embed_height_px=embed_height_px,
        page_width_pt=page_width_pt,
        page_height_pt=page_height_pt,
    )
    round_tripped = textract_bbox_to_canonical(
        bbox,
        embed_width_px=embed_width_px,
        embed_height_px=embed_height_px,
        page_width_pt=page_width_pt,
        page_height_pt=page_height_pt,
    )
    for original, back in zip(rect, round_tripped, strict=True):
        assert back == pytest.approx(original, abs=0.5)


def test_textract_bbox_to_canonical_matches_hand_computed_example() -> None:
    # A line box at the exact vertical/horizontal center-left of a Letter page, hand-computed:
    # embed render of a 612x792 page at the 1568px cap is 1210x1568 (long edge = height).
    # left=0.1, top=0.5, width=0.3, height=0.02 of that image.
    bbox = NormalizedBoundingBox(left=0.1, top=0.5, width=0.3, height=0.02)
    embed_width_px, embed_height_px = embed_render_pixel_size(
        page_width_pt=612.0, page_height_pt=792.0, max_long_edge_px=1568
    )
    rect = textract_bbox_to_canonical(
        bbox,
        embed_width_px=embed_width_px,
        embed_height_px=embed_height_px,
        page_width_pt=612.0,
        page_height_pt=792.0,
    )
    assert rect[0] == pytest.approx(0.1 * 612.0, abs=0.5)
    assert rect[1] == pytest.approx(0.5 * 792.0, abs=0.5)
    assert rect[2] == pytest.approx((0.1 + 0.3) * 612.0, abs=0.5)
    assert rect[3] == pytest.approx((0.5 + 0.02) * 792.0, abs=0.5)


# -- clamp_rect_to_page -------------------------------------------------------------------------


def test_clamp_rect_to_page_pulls_overhang_back_inside() -> None:
    clamped = clamp_rect_to_page((-5.0, -1.0, 620.0, 800.0), page_width=612.0, page_height=792.0)
    assert clamped == (0.0, 0.0, 612.0, 792.0)


def test_clamp_rect_to_page_is_a_no_op_for_an_in_bounds_rect() -> None:
    rect = (72.0, 100.0, 500.0, 120.0)
    assert clamp_rect_to_page(rect, page_width=612.0, page_height=792.0) == rect


# -- clip_rect_horizontal -----------------------------------------------------------------------


def test_clip_rect_horizontal_mid_line_is_narrower_and_on_the_right_side() -> None:
    line_rect = (72.0, 140.0, 539.71, 155.11)
    # "The facility achieved 94% uptime in Q3." is the first 40 of 101 characters of the line.
    sentence_end_fraction = 40 / 101
    clipped = clip_rect_horizontal(
        line_rect, start_fraction=0.0, end_fraction=sentence_end_fraction
    )
    assert clipped[0] == line_rect[0]  # unchanged left edge — sentence starts at line start
    assert clipped[2] < line_rect[2]  # clipped short of the full line width
    assert clipped[1] == line_rect[1]
    assert clipped[3] == line_rect[3]
    width = line_rect[2] - line_rect[0]
    assert clipped[2] == pytest.approx(line_rect[0] + width * sentence_end_fraction)


def test_clip_rect_horizontal_adjacent_sentences_do_not_overlap_by_more_than_2pt() -> None:
    line_rect = (72.0, 140.0, 539.71, 155.11)
    sentence_a = clip_rect_horizontal(line_rect, start_fraction=0.0, end_fraction=40 / 101)
    sentence_b = clip_rect_horizontal(line_rect, start_fraction=40 / 101, end_fraction=1.0)
    overlap = sentence_a[2] - sentence_b[0]
    assert overlap <= 2.0


def test_clip_rect_horizontal_rejects_invalid_fractions() -> None:
    with pytest.raises(ValueError, match="invalid clip fractions"):
        clip_rect_horizontal((0.0, 0.0, 100.0, 10.0), start_fraction=0.6, end_fraction=0.4)
