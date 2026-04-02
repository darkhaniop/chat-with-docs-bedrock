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


# Padding sentences for the three text-layer PDF fixtures below. docs/03-ingestion.md#step-1-
# probe classifies a page as textSource="pdf" only once character density crosses
# `text_density_threshold` (250 chars/sq-in * 0.15 ~= 3500 characters on a Letter page) — the
# fixtures' original two- or three-sentence bodies (Phase 0) were realistic-looking but far too
# sparse to cross that threshold, so they silently fell through to the `"textract"` path and
# never exercised PyMuPDF's native text extraction at all (found in Phase 3 when `test_probe.py`
# asserted `text_source == "pdf"` and got `"textract"` instead). Each of these sentences is
# distinct (not a repeated filler line) so later chunking/sentence-segmentation tests have real
# sentence boundaries to work with, not a suspiciously repetitive corpus.
_FACILITY_PADDING = [
    "Energy consumption per square foot fell to its lowest level since the facility opened.",
    "The backup generator was tested twice during the quarter with no issues found.",
    "A total of forty-one work orders were closed within the target service window.",
    "Water usage remained within the seasonal average despite the extended dry spell.",
    "The loading dock resurfacing project was completed two weeks ahead of schedule.",
    "Staff turnover in the facilities team was zero for the third consecutive quarter.",
    "The HVAC control system firmware was updated to the latest vendor release.",
    "Three vendor contracts were renewed on existing terms without material changes.",
    "The quarterly fire suppression inspection found all systems fully operational.",
    "Parking lot lighting was upgraded to LED fixtures, reducing nightly draw by 30%.",
    "A new access control reader was installed at the north entrance in September.",
    "The roof membrane inspection identified no areas requiring immediate repair.",
    "Recycling volume increased by 18% following the relaunch of the sorting program.",
    "The elevator modernization project remains on track for a Q1 completion date.",
    "Two new preventive maintenance checklists were added to the CMMS this quarter.",
    "The break room renovation was completed within the approved budget envelope.",
    "Annual boiler certification was renewed following a successful inspection visit.",
    "The facility's stormwater management plan was updated to reflect new regulations.",
    "A pest control audit found no evidence of activity in any monitored zone.",
    "The loading dock's overhead doors received scheduled spring maintenance service.",
    "Signage throughout the facility was updated to the new corporate branding standard.",
    "The security camera network was expanded to cover the newly leased annex space.",
    "Landscaping contracts were consolidated under a single regional vendor this quarter.",
    "The facility passed its annual insurance carrier walkthrough without any findings.",
    "Contractor safety orientation was completed for all new vendors prior to site access.",
    "The emergency lighting system was fully tested during the September fire drill.",
    "Janitorial staffing was increased on weekends to cover the new tenant floor.",
    "The facility's electrical switchgear received its five-year inspection this cycle.",
    "A leak in the third-floor supply line was identified and repaired within a day.",
    "The visitor management kiosk was replaced with an updated hardware model.",
    "The annual budget review identified three areas eligible for cost reduction next year.",
    "A new vendor was onboarded for glass and window cleaning across all leased floors.",
    "The facility's emergency response plan was revised to include the new annex wing.",
    "Quarterly air quality testing returned results within all regulatory thresholds.",
    "The bicycle storage room was expanded to accommodate increased tenant demand.",
    "A scheduled power outage for panel maintenance was completed with no disruption.",
    "The facility's insurance policy was renewed at a lower premium than last year.",
    "New directional signage was installed to improve wayfinding on the second floor.",
    "The loading dock scheduling system was upgraded to reduce truck idling time.",
    "A tenant satisfaction survey returned its highest score in the past three years.",
    "The rooftop solar array produced 8% more energy than the same quarter last year.",
    "Two long-standing maintenance requests were closed after parts finally arrived.",
    "The facility's key card system was audited and twelve inactive cards were revoked.",
    "A new recycling stream for electronic waste was introduced in the break rooms.",
    "The quarterly town hall for facilities staff covered the upcoming renovation plan.",
    "A revised vendor scorecard was introduced to track on-time completion rates.",
    "The facility's noise monitoring program logged no exceedances during the quarter.",
    "An updated evacuation map was posted on every floor following the annex expansion.",
    "The janitorial contract was extended for one year on the same negotiated rate.",
    "A new digital work order form reduced average request intake time by half.",
]

_RESEARCH_PADDING_LEFT = [
    "Prior work on retrieval-augmented generation has largely treated chunking as a fixed "
    "preprocessing step rather than a tunable parameter of the overall system.",
    "We define a chunk boundary as correct if it does not split a sentence that a human "
    "annotator judged to be a single semantic unit.",
    "The held-out corpus spans technical manuals, financial filings, and scientific papers "
    "to avoid overfitting the comparison to a single document genre.",
    "Fixed-width chunking was implemented with a sliding window and no sentence awareness, "
    "serving as the baseline for every comparison in this note.",
    "Paragraph-based chunking preserved more local context but produced highly variable "
    "chunk sizes, complicating downstream token budgeting.",
    "We measured both retrieval recall and the rate of citations that pointed to the wrong "
    "sentence within an otherwise correctly retrieved chunk.",
    "Annotators were shown candidate chunk boundaries without knowing which strategy "
    "produced them, to avoid biasing the manual review.",
    "The evaluation set was frozen before any chunking strategy was implemented, to prevent "
    "the boundary definitions from being tuned to the test data.",
    "Chunk overlap was varied independently of chunk size to isolate its effect on "
    "boundary-loss recovery.",
    "All three strategies were run against the same embedding model and retrieval index "
    "to keep the comparison isolated to chunking behavior alone.",
]

_RESEARCH_PADDING_RIGHT = [
    "A secondary analysis restricted to tables and figures showed a smaller but still "
    "significant advantage for the sentence-aware strategy.",
    "Latency overhead from sentence segmentation was under five milliseconds per page, "
    "well within the ingestion pipeline's per-page budget.",
    "The fixed-width baseline's failures clustered heavily around numbered lists and "
    "multi-clause sentences with embedded citations.",
    "Manual review of a sample of one hundred citations found no cases where the "
    "sentence-aware strategy introduced a new failure mode.",
    "We did not observe a meaningful difference in indexing cost between the strategies, "
    "since token count dominates cost far more than segmentation choice.",
    "Future work should evaluate whether a learned boundary model outperforms the "
    "rules-based sentence splitter used here.",
    "The one-sentence overlap setting was chosen after a small sweep over zero, one, and "
    "two sentences of overlap on a validation split.",
    "Two-sentence overlap recovered marginally more boundary-loss cases but increased "
    "average chunk size beyond the target token range.",
    "We recommend the sentence-aware strategy with one-sentence overlap as the default "
    "for prose-heavy technical corpora going forward.",
    "This note does not address chunking for pages dominated by tables, which we treat "
    "as a separate problem in a companion study.",
    "A follow-up experiment measured wall-clock indexing time across all three "
    "strategies on a shared hardware configuration.",
    "The sentence-aware strategy added roughly three percent to total indexing time, "
    "a cost we consider acceptable given the recall improvement.",
    "We also examined whether chunk overlap should vary by document type rather than "
    "using a single fixed value across the corpus.",
    "Preliminary results suggest technical manuals benefit from slightly larger overlap "
    "than financial filings, though the sample size here is small.",
]


def born_digital_pdf() -> None:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)  # US Letter
    page.insert_text((72, 100), "Quarterly Facilities Report", fontsize=18)
    body = (
        "The facility achieved 94% uptime in Q3. This was attributable to the new cooling "
        "loop installed in August. Maintenance costs decreased by 12% relative to Q2.\n\n"
        "Staffing levels remained stable throughout the quarter. No safety incidents were "
        "recorded. The next scheduled inspection is in January.\n\n" + " ".join(_FACILITY_PADDING)
    )
    page.insert_textbox((72, 140, 540, 760), body, fontsize=9)
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
        "corpus of technical documents. " + " ".join(_RESEARCH_PADDING_LEFT)
    )
    right = (
        "Results. The sentence-aware strategy outperformed fixed-width chunking by 8 points "
        "of recall at k=10. Overlap of one sentence between adjacent chunks recovered most of "
        "the boundary-loss cases without materially increasing token cost. "
        + " ".join(_RESEARCH_PADDING_RIGHT)
    )
    page.insert_textbox((72, 110, 290, 760), left, fontsize=8)
    page.insert_textbox((322, 110, 540, 760), right, fontsize=8)
    doc.save(FIXTURES_DIR / "two-column.pdf")
    doc.close()


def rotated_pdf() -> None:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Rotated Page Fixture", fontsize=16)
    page.insert_textbox(
        (72, 140, 540, 760),
        "This page is stored with a 90 degree rotation flag. PyMuPDF must normalise the "
        "extracted line rectangles using the page's rotation matrix at extraction time, not "
        "at render time, or the highlight will land in the wrong place when viewed upright. "
        + " ".join(_FACILITY_PADDING),
        fontsize=9,
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


def five_page_pdf() -> None:
    # docs/08-testing.md's `test_ingest_pipeline`: "Upload a 5-page fixture PDF, run the state
    # machine, assert READY, chunk count, ... and one hand-checked sentence's rect." Each page
    # reuses the same dense padding pool born-digital.pdf uses, so every page independently
    # crosses the text-density threshold and gets textSource="pdf" (docs/03-ingestion.md#step-1).
    doc = fitz.open()
    for page_number in range(1, 6):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 100), f"Five-Page Fixture, Page {page_number}", fontsize=16)
        opening = (
            f"This is page {page_number} of a five-page fixture used to exercise the "
            "Distributed Map's multi-page fan-out and chunk numbering across pages. "
        )
        body = opening + " ".join(_FACILITY_PADDING)
        page.insert_textbox((72, 140, 540, 760), body, fontsize=9)
    doc.save(FIXTURES_DIR / "five-page.pdf")
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
    five_page_pdf()
    photograph_jpg()
    for path in sorted(FIXTURES_DIR.glob("*")):
        if path.is_file() and path.suffix in (".pdf", ".jpg"):
            print(f"{path.name}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
