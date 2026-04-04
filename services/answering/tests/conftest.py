"""moto-backed DynamoDB for `services/answering` unit tests — mirrors
`services/ingestion/tests/conftest.py`'s pattern, minus S3/Textract, which nothing in
`answering` touches yet (Phase 5/6 add the S3 renders → prompt-image path)."""

from __future__ import annotations

from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws

from common.config import get_settings
from common.repo import Repo


@pytest.fixture
def repo() -> Iterator[Repo]:
    with mock_aws():
        settings = get_settings()
        client = boto3.client("dynamodb", region_name=settings.aws_region)
        client.create_table(
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
        yield Repo(client, table_name=settings.table_name)
