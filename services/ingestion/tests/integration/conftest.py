"""Fixtures for `pytest -m integration` against the real deployed `dev` stack
(docs/08-testing.md#integration-tests). Needs the same env vars as
`services/api/tests/integration/conftest.py` (`CWD_API_BASE_URL`, `CWD_USER_POOL_ID`,
`CWD_USER_POOL_CLIENT_ID`) plus real AWS credentials for the direct DynamoDB reads these tests
need — no HTTP route exposes Page/Chunk items yet (that's Phase 5's job).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
import pytest

from common.config import get_settings
from common.repo import Repo

_E2E_ENV_FILE = Path(__file__).resolve().parents[4] / "e2e" / "fixtures" / ".env"
FIXTURES_DIR = Path(__file__).resolve().parents[4] / "e2e" / "fixtures"


@pytest.fixture(autouse=True)
def aws_stack() -> Iterator[None]:
    """Overrides `services/ingestion/tests/conftest.py`'s autouse moto fixture — see
    `services/api/tests/integration/conftest.py`'s identical override for the full explanation
    (autouse fixtures cascade into subdirectories; without this, real AWS calls here would be
    silently intercepted by moto instead of hitting the deployed stack)."""
    yield


def _load_e2e_env_file() -> dict[str, str]:
    if not _E2E_ENV_FILE.exists():
        return {}
    values: dict[str, str] = {}
    for line in _E2E_ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


@dataclass(frozen=True)
class CognitoConfig:
    user_pool_id: str
    client_id: str
    api_base_url: str


@pytest.fixture(scope="session")
def cognito_config() -> CognitoConfig:
    missing = [
        name
        for name in ("CWD_API_BASE_URL", "CWD_USER_POOL_ID", "CWD_USER_POOL_CLIENT_ID")
        if not os.environ.get(name)
    ]
    if missing:
        pytest.skip(f"integration test needs {', '.join(missing)} set (see conftest.py)")
    return CognitoConfig(
        user_pool_id=os.environ["CWD_USER_POOL_ID"],
        client_id=os.environ["CWD_USER_POOL_CLIENT_ID"],
        api_base_url=os.environ["CWD_API_BASE_URL"].rstrip("/"),
    )


@pytest.fixture(scope="session")
def cognito_idp() -> object:
    return boto3.client("cognito-idp", region_name=os.environ.get("CWD_AWS_REGION", "us-east-1"))


@pytest.fixture(scope="session")
def seeded_user_token(cognito_idp: object, cognito_config: CognitoConfig) -> str:
    env = _load_e2e_env_file()
    email = os.environ.get("CWD_E2E_TEST_USER_EMAIL") or env.get("CWD_E2E_TEST_USER_EMAIL")
    password = os.environ.get("CWD_E2E_TEST_USER_PASSWORD") or env.get("CWD_E2E_TEST_USER_PASSWORD")
    if not email or not password:
        pytest.skip("seeded e2e test user credentials not found (e2e/fixtures/.env)")
    response = cognito_idp.admin_initiate_auth(  # type: ignore[attr-defined]
        UserPoolId=cognito_config.user_pool_id,
        ClientId=cognito_config.client_id,
        AuthFlow="ADMIN_USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": email, "PASSWORD": password},
    )
    id_token: str = response["AuthenticationResult"]["IdToken"]
    return id_token


@pytest.fixture(scope="session")
def repo() -> Repo:
    settings = get_settings()
    client = boto3.client("dynamodb", region_name=settings.aws_region)
    return Repo(client, table_name=settings.table_name)


# -- shared HTTP helpers (bundled behind a fixture, not a plain sibling import — pytest's
# --import-mode=importlib does not add a test file's own directory to sys.path, so
# `from _helpers import ...` in a sibling test module doesn't resolve; conftest.py fixtures are
# the one thing pytest resolves specially regardless of import mode) -----------------------------


def _request(
    method: str, url: str, *, token: str, body: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode() if body is not None else None
    http_request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(http_request) as response:  # noqa: S310
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _upload_fixture(
    cognito_config: CognitoConfig, token: str, filename: str, content_type: str, *, name_suffix: str
) -> tuple[str, str]:
    data = (FIXTURES_DIR / filename).read_bytes()
    status, body = _request(
        "POST",
        f"{cognito_config.api_base_url}/projects",
        token=token,
        body={"name": f"cwd-integration-{name_suffix}-{filename}"},
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


@dataclass(frozen=True)
class ApiHelpers:
    request: Any = _request
    upload_fixture: Any = _upload_fixture
    wait_for_terminal_status: Any = _wait_for_terminal_status


@pytest.fixture(scope="session")
def api() -> ApiHelpers:
    return ApiHelpers()
