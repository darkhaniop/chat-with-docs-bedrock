"""Text-density classification over the fixture corpus (docs/08-testing.md, docs/10-roadmap.md
Phase 3 task 2). The threshold is a tuning constant, so these tests pin *behavior* — which
fixtures land on which side — rather than exact density numbers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from common.config import Settings
from ingestion.probe import (
    ProbeError,
    classify_text_source,
    probe,
    text_density,
    validate_magic_bytes,
)

_FIXTURES = Path(__file__).parents[3] / "e2e" / "fixtures"


@pytest.fixture
def settings() -> Settings:
    return Settings()


def _read(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


# -- magic bytes --------------------------------------------------------------------------------


def test_validate_magic_bytes_accepts_a_real_pdf() -> None:
    validate_magic_bytes("application/pdf", _read("born-digital.pdf"))


def test_validate_magic_bytes_rejects_mismatched_content_type() -> None:
    with pytest.raises(ProbeError, match="does not match"):
        validate_magic_bytes("image/png", _read("born-digital.pdf"))


def test_validate_magic_bytes_accepts_a_real_jpeg() -> None:
    validate_magic_bytes("image/jpeg", _read("photograph.jpg"))


def test_validate_magic_bytes_rejects_unsupported_content_type() -> None:
    with pytest.raises(ProbeError, match="Unsupported content type"):
        validate_magic_bytes("application/zip", b"PK\x03\x04")


# -- text density formula ------------------------------------------------------------------------


def test_text_density_is_clamped_to_zero_and_one() -> None:
    assert text_density(0, width_pt=612, height_pt=792, chars_per_sq_inch=250) == 0.0
    huge = text_density(1_000_000, width_pt=612, height_pt=792, chars_per_sq_inch=250)
    assert huge == 1.0


def test_classify_text_source_uses_the_configured_threshold() -> None:
    assert classify_text_source(0.20, threshold=0.15) == "pdf"
    assert classify_text_source(0.10, threshold=0.15) == "textract"
    assert classify_text_source(0.15, threshold=0.15) == "pdf"  # boundary is inclusive


# -- probe() over the real fixture corpus --------------------------------------------------------


def test_probe_born_digital_pdf_classifies_as_pdf_text_source(settings: Settings) -> None:
    result = probe(_read("born-digital.pdf"), "application/pdf", settings)
    assert result.kind == "pdf"
    assert result.page_count == 1
    assert result.ocr_page_count == 0
    assert result.pages[0].text_source == "pdf"
    assert result.pages[0].width == pytest.approx(612.0)
    assert result.pages[0].height == pytest.approx(792.0)


def test_probe_two_column_pdf_classifies_as_pdf_text_source(settings: Settings) -> None:
    result = probe(_read("two-column.pdf"), "application/pdf", settings)
    assert result.pages[0].text_source == "pdf"


def test_probe_scanned_pdf_classifies_as_textract(settings: Settings) -> None:
    result = probe(_read("scanned.pdf"), "application/pdf", settings)
    assert result.page_count == 1
    assert result.ocr_page_count == 1
    assert result.pages[0].text_source == "textract"
    assert result.pages[0].text_density < settings.text_density_threshold


def test_probe_rotated_pdf_reports_the_post_rotation_dimensions(settings: Settings) -> None:
    result = probe(_read("rotated.pdf"), "application/pdf", settings)
    page = result.pages[0]
    assert page.rotation == 90
    # PyMuPDF's page.rect already swaps width/height for a 90-degree rotation (see
    # common/geometry.py's module docstring) — the mediabox is 612x792, but the canonical,
    # post-rotation page is 792x612.
    assert page.width == pytest.approx(792.0)
    assert page.height == pytest.approx(612.0)
    assert page.text_source == "pdf"


def test_probe_slide_export_pdf(settings: Settings) -> None:
    result = probe(_read("slide-export.pdf"), "application/pdf", settings)
    assert result.pages[0].width == pytest.approx(960.0)
    assert result.pages[0].height == pytest.approx(540.0)


def test_probe_slide_export_pdf_chart_page_is_image_only_and_routes_to_textract(
    settings: Settings,
) -> None:
    """Page 2 (docs/08-testing.md#citation-fidelity-evaluation's "chart question" fixture) is a
    raster bar chart with no PDF text layer — same shape as `scanned.pdf`, just embedded as a
    second page of an otherwise text-layer PDF rather than its own document."""
    result = probe(_read("slide-export.pdf"), "application/pdf", settings)
    assert result.page_count == 2
    chart_page = result.pages[1]
    assert chart_page.text_source == "textract"
    assert chart_page.text_density == 0.0


def test_probe_photograph_is_one_page_textract_unconditionally(settings: Settings) -> None:
    result = probe(_read("photograph.jpg"), "image/jpeg", settings)
    assert result.kind == "image"
    assert result.page_count == 1
    assert result.ocr_page_count == 1
    assert result.pages[0].text_source == "textract"
    assert result.pages[0].width == pytest.approx(1200.0)
    assert result.pages[0].height == pytest.approx(900.0)


def test_probe_rejects_a_page_count_over_the_limit(settings: Settings) -> None:
    small_settings = settings.model_copy(update={"max_document_pages": 0})
    with pytest.raises(ProbeError, match="exceeds"):
        probe(_read("born-digital.pdf"), "application/pdf", small_settings)


def test_probe_rejects_a_byte_size_over_the_limit(settings: Settings) -> None:
    small_settings = settings.model_copy(update={"max_document_bytes": 10})
    with pytest.raises(ProbeError, match="limit is 10"):
        probe(_read("born-digital.pdf"), "application/pdf", small_settings)


def test_probe_rejects_a_corrupt_pdf(settings: Settings) -> None:
    with pytest.raises(ProbeError, match="Corrupt"):
        probe(b"%PDF-1.4 not actually a pdf", "application/pdf", settings)


def test_probe_rejects_a_corrupt_image(settings: Settings) -> None:
    with pytest.raises(ProbeError, match="Corrupt"):
        probe(b"\x89PNG\r\n\x1a\nnot actually a png", "image/png", settings)


def test_probe_image_dimensions_reflect_exif_rotation(settings: Settings) -> None:
    # A landscape raster (300x200) carrying an EXIF tag that says "rotate 90 CW to display
    # upright" must probe as portrait (200x300) — matching what render.py's exif_transpose'd
    # re-encode will actually produce (see probe_image's docstring).
    import io

    from PIL import Image

    image = Image.new("RGB", (300, 200), "white")
    exif = image.getexif()
    exif[0x0112] = 6  # Orientation tag: "rotate 90 CW to display upright"
    buf = io.BytesIO()
    image.save(buf, format="jpeg", exif=exif.tobytes())

    result = probe(buf.getvalue(), "image/jpeg", settings)

    assert (result.pages[0].width, result.pages[0].height) == (200.0, 300.0)
