"""Fixtures for `pytest -m integration` against the real deployed `dev` stack
(docs/08-testing.md#integration-tests). Needs `CWD_INTEGRATION=1`, AWS credentials (e.g.
`AWS_PROFILE=cdk-dev`), and three env vars this project doesn't otherwise define because
they're deploy-time outputs, not naming-convention-derivable
(docs/09-operations.md#deployment writes them to `infra/cdk-outputs.dev.json`):

    CWD_API_BASE_URL        e.g. https://abc123.execute-api.us-east-1.amazonaws.com
    CWD_USER_POOL_ID        e.g. us-east-1_amS2R0VF0
    CWD_USER_POOL_CLIENT_ID the web app client id

The seeded test user's own credentials (prerequisite 7) are read separately from
`e2e/fixtures/.env` — `CWD_E2E_TEST_USER_EMAIL` / `CWD_E2E_TEST_USER_PASSWORD` — since that
file already exists for the (not yet built) Playwright global setup and there is no reason for
two copies of the same secret.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import boto3
import pytest

_E2E_ENV_FILE = Path(__file__).resolve().parents[4] / "e2e" / "fixtures" / ".env"


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


def _admin_password_auth(
    cognito_idp: object, config: CognitoConfig, *, email: str, password: str
) -> str:
    response = cognito_idp.admin_initiate_auth(  # type: ignore[attr-defined]
        UserPoolId=config.user_pool_id,
        ClientId=config.client_id,
        AuthFlow="ADMIN_USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": email, "PASSWORD": password},
    )
    id_token: str = response["AuthenticationResult"]["IdToken"]
    return id_token


@pytest.fixture(scope="session")
def seeded_user_token(cognito_idp: object, cognito_config: CognitoConfig) -> str:
    env = _load_e2e_env_file()
    email = os.environ.get("CWD_E2E_TEST_USER_EMAIL") or env.get("CWD_E2E_TEST_USER_EMAIL")
    password = os.environ.get("CWD_E2E_TEST_USER_PASSWORD") or env.get("CWD_E2E_TEST_USER_PASSWORD")
    if not email or not password:
        pytest.skip("seeded e2e test user credentials not found (e2e/fixtures/.env)")
    return _admin_password_auth(cognito_idp, cognito_config, email=email, password=password)


@pytest.fixture
def second_user_token(cognito_idp: object, cognito_config: CognitoConfig) -> Iterator[str]:
    """An ephemeral second Cognito user, created and torn down within one test — proves
    cross-tenant 404 without needing a second permanently-seeded human account."""
    email = f"cwd-integration-{uuid.uuid4().hex[:12]}@example.com"
    password = "Integration-Test-Password-1"
    cognito_idp.admin_create_user(  # type: ignore[attr-defined]
        UserPoolId=cognito_config.user_pool_id,
        Username=email,
        UserAttributes=[
            {"Name": "email", "Value": email},
            {"Name": "email_verified", "Value": "true"},
        ],
        MessageAction="SUPPRESS",
    )
    cognito_idp.admin_set_user_password(  # type: ignore[attr-defined]
        UserPoolId=cognito_config.user_pool_id,
        Username=email,
        Password=password,
        Permanent=True,
    )
    try:
        yield _admin_password_auth(cognito_idp, cognito_config, email=email, password=password)
    finally:
        cognito_idp.admin_delete_user(  # type: ignore[attr-defined]
            UserPoolId=cognito_config.user_pool_id, Username=email
        )
