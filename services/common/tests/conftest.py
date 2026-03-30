"""moto-backed fixtures for repo/storage/authz unit tests — no network, no real AWS
(docs/08-testing.md#strategy: unit tests never touch the network; moto mocks the AWS API at the
transport layer so the code under test still exercises real boto3 request/response shapes)."""

from __future__ import annotations

from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws

from common.config import get_settings
from common.repo import Repo
from common.storage import DocumentsStore

_TABLE_NAME = "cwd-test"
_BUCKET_NAME = "cwd-documents-test-111122223333"


@pytest.fixture
def dynamodb_client() -> Iterator[object]:
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=_TABLE_NAME,
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
        yield client


@pytest.fixture
def repo(dynamodb_client: object) -> Repo:
    return Repo(dynamodb_client, table_name=_TABLE_NAME)


@pytest.fixture
def s3_client() -> Iterator[object]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=_BUCKET_NAME)
        yield client


@pytest.fixture
def store(s3_client: object) -> DocumentsStore:
    return DocumentsStore(s3_client, get_settings(), bucket_name=_BUCKET_NAME)
