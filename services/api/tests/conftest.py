"""moto-backed AWS for handler-level routing tests. Uses the same table/bucket-naming
convention `common.config.get_settings()` already resolves to by default (env `dev`), so the
Lambda's own dependency wiring (`api.deps`) needs no test-only env override beyond the bucket
name, which is genuinely deploy-time-injected (see `api/deps.py`)."""

from __future__ import annotations

from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws

from api import deps
from common.config import get_settings

_BUCKET_NAME = "cwd-documents-test-111122223333"


@pytest.fixture(autouse=True)
def aws_stack(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CWD_DOCUMENTS_BUCKET_NAME", _BUCKET_NAME)
    with mock_aws():
        settings = get_settings()
        dynamodb = boto3.client("dynamodb", region_name=settings.aws_region)
        dynamodb.create_table(
            TableName=settings.table_name,
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        s3 = boto3.client("s3", region_name=settings.aws_region)
        s3.create_bucket(Bucket=_BUCKET_NAME)

        deps.get_repo.cache_clear()
        deps.get_store.cache_clear()
        yield
        deps.get_repo.cache_clear()
        deps.get_store.cache_clear()
