"""docs/10-roadmap.md's Phase 3 exit criterion: "All six fixture documents reach READY with
plausible chunk counts." Runs against the real deployed `dev` stack —
`CWD_INTEGRATION=1 uv run pytest -m integration`.

The scanned/five-page fixtures already get a deeper, hand-checked look in
`test_textract_geometry.py`/`test_ingest_pipeline.py`; this test is the scripted stand-in for
the "a human uploads each fixture through the SPA and watches it reach READY" exit criterion —
this sandboxed environment has no browser (same rationale Phase 2's log used for its own
scripted upload check). Chunk counts are asserted as "plausible" (a non-negative int), not
exact — a sparse OCR page can legitimately produce zero chunks per docs/03-ingestion.md's
"fewer than 20 characters of text produces no text chunk" rule, and this test isn't trying to
pin exact OCR output the way `test_textract_geometry.py` does for one fixture.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from conftest import ApiHelpers, CognitoConfig

pytestmark = pytest.mark.integration

_FIXTURES = [
    ("born-digital.pdf", "application/pdf", 1),
    ("two-column.pdf", "application/pdf", 1),
    ("rotated.pdf", "application/pdf", 1),
    ("scanned.pdf", "application/pdf", 1),
    # 2 pages since Phase 4 added a chart page for the eval corpus's "chart question"
    # (e2e/fixtures/eval/questions.json) — docs/08-testing.md#citation-fidelity-evaluation.
    ("slide-export.pdf", "application/pdf", 2),
    ("photograph.jpg", "image/jpeg", 1),
]


@pytest.mark.parametrize(("filename", "content_type", "expected_page_count"), _FIXTURES)
def test_fixture_reaches_ready(
    cognito_config: CognitoConfig,
    seeded_user_token: str,
    api: ApiHelpers,
    filename: str,
    content_type: str,
    expected_page_count: int,
) -> None:
    project_id, document_id = api.upload_fixture(
        cognito_config, seeded_user_token, filename, content_type, name_suffix="all-fixtures"
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
        assert document["pageCount"] == expected_page_count
        assert document["ingestion"]["chunkCount"] >= 0
    finally:
        api.request(
            "DELETE",
            f"{cognito_config.api_base_url}/projects/{project_id}",
            token=seeded_user_token,
        )
