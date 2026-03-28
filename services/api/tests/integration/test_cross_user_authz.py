"""docs/10-roadmap.md Phase 2 exit criterion: "A second user's token gets 404 on the first
user's project." Runs against the real deployed `dev` stack — `CWD_INTEGRATION=1 uv run pytest
-m integration` (see `conftest.py` for the env vars it needs).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
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


def test_a_second_users_token_gets_404_on_the_first_users_project(
    cognito_config: CognitoConfig, seeded_user_token: str, second_user_token: str
) -> None:
    status, body = _request(
        "POST",
        f"{cognito_config.api_base_url}/projects",
        token=seeded_user_token,
        body={"name": "cwd-integration-cross-user-test"},
    )
    assert status == 201, body
    project_id = body["projectId"]

    try:
        status, body = _request(
            "GET",
            f"{cognito_config.api_base_url}/projects/{project_id}",
            token=second_user_token,
        )
        assert status == 404, body
        assert body["error"]["code"] == "NOT_FOUND"

        status, body = _request(
            "GET",
            f"{cognito_config.api_base_url}/projects/{project_id}/documents",
            token=second_user_token,
        )
        assert status == 404, body
    finally:
        _request(
            "DELETE",
            f"{cognito_config.api_base_url}/projects/{project_id}",
            token=seeded_user_token,
        )
