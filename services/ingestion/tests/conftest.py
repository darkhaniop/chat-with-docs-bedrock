"""moto-backed AWS for ingestion handler tests — mirrors services/api/tests/conftest.py's
pattern (docs/08-testing.md#strategy: unit tests never touch the network)."""

from __future__ import annotations

from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws

from common.config import get_settings
from ingestion import deps

_BUCKET_NAME = "cwd-documents-test-111122223333"


def _clear_caches() -> None:
    # Tests that monkeypatch e.g. `deps.get_events` replace the lru_cache-wrapped function with
    # a plain lambda for the test's duration; by teardown time it may no longer have
    # `cache_clear` (monkeypatch's own undo runs after this fixture's), so clear defensively.
    for getter in (deps.get_repo, deps.get_store, deps.get_textract, deps.get_events):
        clear = getattr(getter, "cache_clear", None)
        if clear is not None:
            clear()


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

        _clear_caches()
        yield
        _clear_caches()
