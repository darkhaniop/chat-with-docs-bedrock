"""docs/08-testing.md's `test_textract_geometry`: "A fixture scan's OCR line boxes, converted
to canonical points, land within the page and match the recorded fixture within tolerance."
Runs against the real deployed `dev` stack — `CWD_INTEGRATION=1 uv run pytest -m integration`.

Real Textract OCR output for a rasterised synthetic image isn't byte-for-byte deterministic the
way PyMuPDF's native text extraction is, so this doesn't hand-check an exact rect the way
`test_ingest_pipeline` does for the born-digital path. What it *does* assert is exactly the
property that matters for correctness: every OCR-derived sentence rect lands inside the
612x792pt page box (docs/08-testing.md#geometry-tests' bounds-containment standard), plus a
content spot-check that the known letter text ("Alvarez") actually made it through OCR.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from conftest import ApiHelpers, CognitoConfig

    from common.repo import Repo

pytestmark = pytest.mark.integration

_PAGE_WIDTH_PT = 612.0
_PAGE_HEIGHT_PT = 792.0


def test_scanned_pdf_produces_textract_derived_chunks_with_in_bounds_rects(
    cognito_config: CognitoConfig, seeded_user_token: str, repo: Repo, api: ApiHelpers
) -> None:
    project_id, document_id = api.upload_fixture(
        cognito_config, seeded_user_token, "scanned.pdf", "application/pdf", name_suffix="textract"
    )
    try:
        status, body = api.request(
            "POST",
            f"{cognito_config.api_base_url}/projects/{project_id}/documents/{document_id}/ingest",
            token=seeded_user_token,
        )
        assert status == 202, body

        document = api.wait_for_terminal_status(
            cognito_config, seeded_user_token, project_id, document_id
        )
        assert document["status"] == "READY", document.get("statusDetail")
        assert document["ingestion"]["ocrPages"] == 1

        pages = repo.list_pages(document_id)
        assert len(pages) == 1
        assert pages[0].text_source == "textract"

        chunks = repo.list_chunks(document_id)
        assert chunks, "scanned fixture produced no chunks — Textract OCR path is broken"

        all_text = " ".join(s.text for c in chunks for s in c.sentences)
        assert "Alvarez" in all_text

        for chunk in chunks:
            for sentence in chunk.sentences:
                for rect in sentence.rects:
                    x0, y0, x1, y1 = rect
                    assert -0.5 <= x0 <= x1 <= _PAGE_WIDTH_PT + 0.5
                    assert -0.5 <= y0 <= y1 <= _PAGE_HEIGHT_PT + 0.5
    finally:
        api.request(
            "DELETE",
            f"{cognito_config.api_base_url}/projects/{project_id}",
            token=seeded_user_token,
        )
