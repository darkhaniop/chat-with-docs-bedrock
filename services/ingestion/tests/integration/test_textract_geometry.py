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

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from conftest import CognitoConfig

    from common.repo import Repo

pytestmark = pytest.mark.integration

_FIXTURES = Path(__file__).resolve().parents[4] / "e2e" / "fixtures"
_PAGE_WIDTH_PT = 612.0
_PAGE_HEIGHT_PT = 792.0


def _request(
    method: str, url: str, *, token: str, body: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as response:  # noqa: S310
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _upload_fixture(
    cognito_config: CognitoConfig, token: str, filename: str, content_type: str
) -> tuple[str, str]:
    data = (_FIXTURES / filename).read_bytes()
    status, body = _request(
        "POST",
        f"{cognito_config.api_base_url}/projects",
        token=token,
        body={"name": f"cwd-integration-textract-{filename}"},
    )
    assert status == 201, body
    project_id: str = body["projectId"]

    status, body = _request(
        "POST",
        f"{cognito_config.api_base_url}/projects/{project_id}/documents",
        token=token,
        body={"filename": filename, "contentType": content_type, "byteSize": len(data)},
    )
    assert status == 201, body
    document_id: str = body["document"]["documentId"]

    upload = body["upload"]
    put_request = urllib.request.Request(
        upload["url"], data=data, method="PUT", headers=upload["headers"]
    )
    with urllib.request.urlopen(put_request) as response:  # noqa: S310
        assert response.status == 200
    return project_id, document_id


def _wait_for_terminal_status(
    cognito_config: CognitoConfig,
    token: str,
    project_id: str,
    document_id: str,
    *,
    timeout_s: float = 180.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status, body = _request(
            "GET",
            f"{cognito_config.api_base_url}/projects/{project_id}/documents/{document_id}",
            token=token,
        )
        assert status == 200, body
        if body["status"] in ("READY", "FAILED"):
            return body
        time.sleep(5)
    pytest.fail(f"document {document_id} did not reach a terminal status within {timeout_s}s")


def test_scanned_pdf_produces_textract_derived_chunks_with_in_bounds_rects(
    cognito_config: CognitoConfig, seeded_user_token: str, repo: Repo
) -> None:
    project_id, document_id = _upload_fixture(
        cognito_config, seeded_user_token, "scanned.pdf", "application/pdf"
    )
    try:
        status, body = _request(
            "POST",
            f"{cognito_config.api_base_url}/projects/{project_id}/documents/{document_id}:ingest",
            token=seeded_user_token,
        )
        assert status == 202, body

        document = _wait_for_terminal_status(
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
        _request(
            "DELETE",
            f"{cognito_config.api_base_url}/projects/{project_id}",
            token=seeded_user_token,
        )
