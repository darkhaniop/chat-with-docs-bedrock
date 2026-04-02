"""PyMuPDF line extraction and rotation normalisation (docs/08-testing.md#geometry-tests).

Hand-checked fixtures: for a single-column page (`born-digital.pdf`), a two-column page
(`two-column.pdf`), and a 90-degree-rotated page (`rotated.pdf`), the expected rect for a
specific line was recorded by running `page.get_text("dict")` directly and inspecting the
result (there is no display available in this environment to pixel-peek a rendered image, so
"hand-checked" here means "read off PyMuPDF's own geometry output and verified it lands where
the inserted text visually should, given the known insertion point" — the same standard the
rotation and reading-order assertions below hold PyMuPDF's rotation handling to).
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from ingestion.extract import extract_pdf_lines

_FIXTURES = Path(__file__).parents[3] / "e2e" / "fixtures"


def _first_page(name: str) -> fitz.Page:
    doc = fitz.open(_FIXTURES / name)
    return doc[0]


# -- single column --------------------------------------------------------------------------


def test_extract_single_column_title_line_matches_hand_checked_rect() -> None:
    lines = extract_pdf_lines(_first_page("born-digital.pdf"))
    title = lines[0]
    assert title.text == "Quarterly Facilities Report"
    assert title.rect == pytest.approx((72.0, 80.65, 280.04, 105.38), abs=0.5)


def test_extract_single_column_body_line_matches_hand_checked_rect() -> None:
    lines = extract_pdf_lines(_first_page("born-digital.pdf"))
    body_line = lines[1]
    assert body_line.text.startswith("The facility achieved 94% uptime in Q3.")
    assert body_line.rect == pytest.approx((72.0, 140.0, 487.69, 152.37), abs=0.5)


# -- two column -------------------------------------------------------------------------------


def test_extract_two_column_reports_both_columns_at_their_own_x_origin() -> None:
    lines = extract_pdf_lines(_first_page("two-column.pdf"))
    left_lines = [line for line in lines if line.rect[0] == pytest.approx(72.0, abs=0.5)]
    right_lines = [line for line in lines if line.rect[0] == pytest.approx(322.0, abs=0.5)]
    assert len(left_lines) > 5
    assert len(right_lines) > 5


def test_extract_two_column_first_left_line_matches_hand_checked_rect() -> None:
    lines = extract_pdf_lines(_first_page("two-column.pdf"))
    # Line index 1 is the first body line (index 0 is the page title).
    first_left_body_line = lines[1]
    assert first_left_body_line.text == "Introduction. Retrieval quality depends heavily on chunk"
    assert first_left_body_line.rect == pytest.approx((72.0, 110.0, 269.87, 120.99), abs=0.5)


def test_extract_two_column_first_right_line_matches_hand_checked_rect() -> None:
    lines = extract_pdf_lines(_first_page("two-column.pdf"))
    right_lines = [line for line in lines if line.rect[0] == pytest.approx(322.0, abs=0.5)]
    assert right_lines[0].text == "Results. The sentence-aware strategy outperformed"
    assert right_lines[0].rect == pytest.approx((322.0, 110.0, 506.52, 120.99), abs=0.5)


# -- rotated page -------------------------------------------------------------------------------


def test_extract_rotated_page_title_matches_hand_checked_rect() -> None:
    page = _first_page("rotated.pdf")
    assert page.rotation == 90
    lines = extract_pdf_lines(page)
    title = lines[0]
    assert title.text == "Rotated Page Fixture"
    # Hand-computed by applying `page.rotation_matrix` to the raw (unrotated) bbox PyMuPDF
    # reports for this line — (72.0, 82.8, 223.18, 104.78) in the original 612x792 content
    # stream — and confirming the result lands inside the canonical 792x612 page box.
    assert title.rect == pytest.approx((687.22, 72.0, 709.2, 223.18), abs=0.5)


def test_extract_rotated_page_every_line_lands_inside_the_page_box() -> None:
    """docs/08-testing.md#geometry-tests: "a 90-degree-rotated page's extracted rects must land
    inside the page box." `page.rect` is already the post-rotation box (792x612 for a 612x792
    mediabox rotated 90 degrees)."""
    page = _first_page("rotated.pdf")
    lines = extract_pdf_lines(page)
    assert len(lines) > 3
    for line in lines:
        x0, y0, x1, y1 = line.rect
        assert 0.0 <= x0 <= x1 <= page.rect.width
        assert 0.0 <= y0 <= y1 <= page.rect.height


# -- lines are dropped when empty, kept in visual (top-to-bottom, per-block) order ------------


def test_extract_skips_whitespace_only_lines() -> None:
    lines = extract_pdf_lines(_first_page("born-digital.pdf"))
    assert all(line.text.strip() for line in lines)


def test_extract_preserves_pymupdf_block_order_within_a_column() -> None:
    lines = extract_pdf_lines(_first_page("born-digital.pdf"))
    y_positions = [line.rect[1] for line in lines]
    assert y_positions == sorted(y_positions)
