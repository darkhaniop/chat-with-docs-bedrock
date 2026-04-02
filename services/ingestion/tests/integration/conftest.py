"""Fixtures for `pytest -m integration` against the real deployed `dev` stack
(docs/08-testing.md#integration-tests). Needs the same env vars as
`services/api/tests/integration/conftest.py` (`CWD_API_BASE_URL`, `CWD_USER_POOL_ID`,
`CWD_USER_POOL_CLIENT_ID`) plus real AWS credentials for the direct DynamoDB reads these tests
need — no HTTP route exposes Page/Chunk items yet (that's Phase 5's job).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import boto3
import pytest

from common.config import get_settings
from common.repo import Repo

_E2E_ENV_FILE = Path(__file__).resolve().parents[4] / "e2e" / "fixtures" / ".env"


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
