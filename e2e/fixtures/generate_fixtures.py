"""Generates the Phase 0 fixture corpus (docs/10-roadmap.md Phase 0 task 9).

Synthetic, not downloaded — small, deterministic, and license-free. Covers the six document
shapes the ingestion pipeline has to handle: a born-digital single-column PDF, a two-column
PDF, a rotated page, a scanned (no text layer) PDF, a slide export, and a standalone
photograph.

Run with: uv run --with pymupdf --with pillow python e2e/fixtures/generate_fixtures.py
Not part of `cwd-check` — it is a one-off generator, not a test.
"""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, ImageDraw

FIXTURES_DIR = Path(__file__).parent


def born_digital_pdf() -> None:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)  # US Letter
    page.insert_text((72, 100), "Quarterly Facilities Report", fontsize=18)
    body = (
        "The facility achieved 94% uptime in Q3. This was attributable to the new cooling "
        "loop installed in August. Maintenance costs decreased by 12% relative to Q2.\n\n"
        "Staffing levels remained stable throughout the quarter. No safety incidents were "
        "recorded. The next scheduled inspection is in January."
    )
    page.insert_textbox((72, 140, 540, 400), body, fontsize=11)
    doc.save(FIXTURES_DIR / "born-digital.pdf")
    doc.close()


def two_column_pdf() -> None:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 80), "Two-Column Research Note", fontsize=16)
    left = (
        "Introduction. Retrieval quality depends heavily on chunk boundaries. Sentences that "
        "are split across chunks lose context, while chunks that are too large dilute "
        "relevance scoring. This note evaluates three chunking strategies on a held-out "
        "corpus of technical documents."
    )
    right = (
        "Results. The sentence-aware strategy outperformed fixed-width chunking by 8 points "
        "of recall at k=10. Overlap of one sentence between adjacent chunks recovered most of "
        "the boundary-loss cases without materially increasing token cost."
    )
    page.insert_textbox((72, 110, 290, 700), left, fontsize=10)
    page.insert_textbox((322, 110, 540, 700), right, fontsize=10)
    doc.save(FIXTURES_DIR / "two-column.pdf")
    doc.close()


def rotated_pdf() -> None:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Rotated Page Fixture", fontsize=16)
    page.insert_textbox(
        (72, 140, 540, 300),
        "This page is stored with a 90 degree rotation flag. PyMuPDF must normalise the "
        "extracted line rectangles using the page's rotation matrix at extraction time, not "
        "at render time, or the highlight will land in the wrong place when viewed upright.",
        fontsize=11,
    )
    page.set_rotation(90)
    doc.save(FIXTURES_DIR / "rotated.pdf")
    doc.close()


def scanned_pdf() -> None:
    # A "scan": a raster image of text with no PDF text layer at all, so ingestion must fall
    # through to Textract OCR (textDensity below threshold; docs/03-ingestion.md#step-1-probe).
    image = Image.new("RGB", (850, 1100), "white")  # ~100 DPI Letter page
    draw = ImageDraw.Draw(image)
    lines = [
        "Dear Ms. Alvarez,",
        "",
        "Thank you for your letter dated March 3rd regarding the",
        "correspondence file. We have located the requested records",
        "and will forward copies within ten business days.",
        "",
        "Sincerely,",
        "Records Office",
    ]
    y = 100
    for line in lines:
        draw.text((75, y), line, fill="black")
        y += 30
    image_path = FIXTURES_DIR / "_scanned_source.jpg"
    image.save(image_path, quality=60)

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(page.rect, filename=str(image_path))
    doc.save(FIXTURES_DIR / "scanned.pdf")
    doc.close()
    image_path.unlink()


def slide_export_pdf() -> None:
    doc = fitz.open()
    page = doc.new_page(width=960, height=540)  # 16:9 slide
    page.insert_text((60, 100), "Q3 Business Review", fontsize=32)
    bullets = [
        "Revenue up 14% quarter over quarter",
        "Two new enterprise customers signed",
        "Churn held flat at 2.1%",
    ]
    y = 200
    for bullet in bullets:
        page.insert_text((80, y), f"• {bullet}", fontsize=18)
        y += 50
    doc.save(FIXTURES_DIR / "slide-export.pdf")
    doc.close()


def photograph_jpg() -> None:
    # A standalone photo-like image with no PDF wrapper at all (docs/00 lists PNG/JPEG/WebP
    # uploads as first-class documents, one page each, textSource="textract" unconditionally).
    image = Image.new("RGB", (1200, 900), "#f2efe9")
    draw = ImageDraw.Draw(image)
    draw.rectangle([40, 40, 1160, 860], outline="#888888", width=6)
    draw.text((100, 100), "Sprint Planning", fill="#1a1a1a")
    draw.line([100, 160, 1100, 160], fill="#1a1a1a", width=3)
    notes = ["- ship citations demo", "- fix rotation bug", "- schedule eval run"]
    y = 220
    for note in notes:
        draw.text((120, y), note, fill="#1a1a1a")
        y += 80
    image.save(FIXTURES_DIR / "photograph.jpg", quality=85)


def main() -> None:
    born_digital_pdf()
    two_column_pdf()
    rotated_pdf()
    scanned_pdf()
    slide_export_pdf()
    photograph_jpg()
    for path in sorted(FIXTURES_DIR.glob("*")):
        if path.is_file() and path.suffix in (".pdf", ".jpg"):
            print(f"{path.name}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
