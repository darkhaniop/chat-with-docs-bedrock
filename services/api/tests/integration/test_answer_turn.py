"""docs/08-testing.md's `test_answer_turn` + docs/10-roadmap.md's Phase 5 exit criterion: "A
question against the fixture corpus returns an answer with >= 1 citation whose
documentId/pageNumber/sentence range are correct by hand inspection." Runs against the real
deployed `dev` stack — `CWD_INTEGRATION=1 uv run pytest -m integration` (see `conftest.py` for
the env vars it needs).

Phase 5 is synchronous (docs/10-roadmap.md#phase-5), so `POST .../messages` here returns the
*resolved* assistant message directly — `{userMessage, assistantMessage}` — not the documented
steady-state `202 {assistantMessageId, channel}` shape Phase 6 restores.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from conftest import CognitoConfig

pytestmark = pytest.mark.integration

FIXTURES_DIR = Path(__file__).resolve().parents[4] / "e2e" / "fixtures"

# born-digital.pdf, not five-page.pdf: this needs a sentence that uniquely identifies *one*
# page. five-page.pdf's body text is deliberately identical padding on every page (it exists to
# exercise the Distributed Map's page fan-out, docs/03-ingestion.md, not citation
# distinctiveness) — the same sentence used here for the assertion actually appears verbatim on
# all five of its pages, found live when this test first ran against a real deploy: the citation
# was completely correct (right text, right rects) but "wrong" page only because the question
# had no unique answer among five identical candidates. born-digital.pdf is a single page, so
# there is no such ambiguity; it's also `e2e/fixtures/eval/questions.json`'s `bd-1`, already
# hand-verified once.
_EXPECTED_PAGE = 1
_EXPECTED_SENTENCE_SUBSTRING = "94% uptime"
_QUESTION = "What was the facility's uptime in Q3?"


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


def _upload_and_ingest(
    cognito_config: CognitoConfig, token: str, filename: str = "five-page.pdf"
) -> tuple[str, str]:
    content_type = "application/pdf"
    data = (FIXTURES_DIR / filename).read_bytes()

    status, body = _request(
        "POST",
        f"{cognito_config.api_base_url}/projects",
        token=token,
        body={"name": "cwd-integration-answer-turn"},
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

    status, body = _request(
        "POST",
        f"{cognito_config.api_base_url}/projects/{project_id}/documents/{document_id}/ingest",
        token=token,
    )
    assert status == 202, body

    deadline = time.monotonic() + 180.0
    while time.monotonic() < deadline:
        status, body = _request(
            "GET",
            f"{cognito_config.api_base_url}/projects/{project_id}/documents/{document_id}",
            token=token,
        )
        assert status == 200, body
        if body["status"] in ("READY", "FAILED"):
            break
        time.sleep(5)
    assert body["status"] == "READY", body.get("statusDetail")

    return project_id, document_id


def test_a_question_with_a_known_answer_resolves_to_the_expected_citation(
    cognito_config: CognitoConfig, seeded_user_token: str
) -> None:
    project_id, document_id = _upload_and_ingest(
        cognito_config, seeded_user_token, "born-digital.pdf"
    )
    try:
        status, body = _request(
            "POST",
            f"{cognito_config.api_base_url}/projects/{project_id}/conversations",
            token=seeded_user_token,
            body={},
        )
        assert status == 201, body
        conversation_id = body["conversationId"]

        status, body = _request(
            "POST",
            f"{cognito_config.api_base_url}/conversations/{conversation_id}/messages",
            token=seeded_user_token,
            body={"text": _QUESTION},
        )
        assert status == 201, body
        assistant_message = body["assistantMessage"]
        assert assistant_message["status"] == "COMPLETE", assistant_message

        citations = assistant_message["citations"]
        assert citations, "expected at least one citation"
        matching = [
            c
            for c in citations
            if c["documentId"] == document_id and c["pageNumber"] == _EXPECTED_PAGE
        ]
        assert matching, (
            f"no citation resolved to page {_EXPECTED_PAGE} of the fixture: {citations}"
        )
        assert any(_EXPECTED_SENTENCE_SUBSTRING in c["citedText"] for c in matching), (
            f"no citation on page {_EXPECTED_PAGE} quoted the expected sentence: {matching}"
        )
        assert not any(c.get("suspect") for c in citations)
    finally:
        _request(
            "DELETE",
            f"{cognito_config.api_base_url}/projects/{project_id}",
            token=seeded_user_token,
        )


def test_a_question_with_no_answer_in_the_documents_cites_nothing(
    cognito_config: CognitoConfig, seeded_user_token: str
) -> None:
    project_id, _document_id = _upload_and_ingest(cognito_config, seeded_user_token)
    try:
        status, body = _request(
            "POST",
            f"{cognito_config.api_base_url}/projects/{project_id}/conversations",
            token=seeded_user_token,
            body={},
        )
        assert status == 201, body
        conversation_id = body["conversationId"]

        status, body = _request(
            "POST",
            f"{cognito_config.api_base_url}/conversations/{conversation_id}/messages",
            token=seeded_user_token,
            body={"text": "What is the capital of France?"},
        )
        assert status == 201, body
        assistant_message = body["assistantMessage"]
        assert assistant_message["status"] == "COMPLETE"
        assert assistant_message["citations"] == []
    finally:
        _request(
            "DELETE",
            f"{cognito_config.api_base_url}/projects/{project_id}",
            token=seeded_user_token,
        )


def test_conversation_lock_rejects_a_second_concurrent_post(
    cognito_config: CognitoConfig, seeded_user_token: str
) -> None:
    """docs/08-testing.md's `test_conversation_lock`: "Two concurrent posts to one conversation:
    one [succeeds], one 409." Phase 5 has no worker/queue yet, so a "concurrent" post here means
    two overlapping synchronous `POST .../messages` calls racing the same conditional
    `claim_lock` — exactly the mechanism docs/10-roadmap.md's Phase 5 task 1 builds, ahead of
    Phase 6's cancellation/streaming layer on top of it."""
    project_id, _document_id = _upload_and_ingest(cognito_config, seeded_user_token)
    try:
        status, body = _request(
            "POST",
            f"{cognito_config.api_base_url}/projects/{project_id}/conversations",
            token=seeded_user_token,
            body={},
        )
        assert status == 201, body
        conversation_id = body["conversationId"]

        def _post(text: str) -> tuple[int, dict[str, Any]]:
            return _request(
                "POST",
                f"{cognito_config.api_base_url}/conversations/{conversation_id}/messages",
                token=seeded_user_token,
                body={"text": text},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(_post, "first concurrent question")
            second = executor.submit(_post, "second concurrent question")
            results = [first.result(), second.result()]

        statuses = sorted(status for status, _ in results)
        assert statuses == [201, 409], results
        blocked_body = next(body for status, body in results if status == 409)
        assert blocked_body["error"]["code"] == "ANSWER_IN_FLIGHT"
    finally:
        _request(
            "DELETE",
            f"{cognito_config.api_base_url}/projects/{project_id}",
            token=seeded_user_token,
        )
