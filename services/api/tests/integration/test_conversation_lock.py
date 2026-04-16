"""docs/08-testing.md's `test_conversation_lock`: "Two concurrent posts to one conversation: one
202, one 409." Runs against the real deployed `dev` stack — `CWD_INTEGRATION=1 uv run pytest -m
integration` (see `conftest.py` for the env vars it needs).

Phase 6: `POST .../messages` enqueues and returns `202` immediately (docs/05-api-contracts.md);
"concurrent" here means two overlapping HTTP posts racing the same conditional `claim_lock`
before either job has necessarily even reached the SQS queue, let alone `answering`. No document
needs to be ingested for this — the lock is claimed and contested before retrieval ever runs.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from conftest import CognitoConfig

pytestmark = pytest.mark.integration


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


def test_two_concurrent_posts_one_202_one_409(
    cognito_config: CognitoConfig, seeded_user_token: str
) -> None:
    status, body = _request(
        "POST",
        f"{cognito_config.api_base_url}/projects",
        token=seeded_user_token,
        body={"name": "cwd-integration-conversation-lock"},
    )
    assert status == 201, body
    project_id = body["projectId"]

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
        assert statuses == [202, 409], results
        accepted_body = next(body for status, body in results if status == 202)
        assert accepted_body["assistantMessageId"]
        assert accepted_body["channel"] == f"/conversations/{conversation_id}"
        blocked_body = next(body for status, body in results if status == 409)
        assert blocked_body["error"]["code"] == "ANSWER_IN_FLIGHT"
    finally:
        _request(
            "DELETE",
            f"{cognito_config.api_base_url}/projects/{project_id}",
            token=seeded_user_token,
        )
