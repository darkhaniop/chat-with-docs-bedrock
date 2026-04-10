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
from common.testing.vectors import FakeVectorIndex

_BUCKET_NAME = "cwd-documents-test-111122223333"
_VECTOR_BUCKET_NAME = "cwd-vectors-test-111122223333"
_STATE_MACHINE_DEFINITION = (
    '{"StartAt": "Probe", "States": {"Probe": {"Type": "Pass", "End": true}}}'
)


@pytest.fixture
def fake_vector_index(monkeypatch: pytest.MonkeyPatch) -> FakeVectorIndex:
    """`moto` doesn't mock `s3vectors` — `api.deps.get_vector_index` is monkeypatched to a fake
    the same way `services/ingestion`'s handler tests already do, rather than hitting real AWS."""
    fake = FakeVectorIndex()
    monkeypatch.setattr(deps, "get_vector_index", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def aws_stack(
    monkeypatch: pytest.MonkeyPatch,
    fake_vector_index: FakeVectorIndex,
) -> Iterator[None]:
    monkeypatch.setenv("CWD_DOCUMENTS_BUCKET_NAME", _BUCKET_NAME)
    monkeypatch.setenv("CWD_VECTOR_BUCKET_NAME", _VECTOR_BUCKET_NAME)
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

        sfn = boto3.client("stepfunctions", region_name=settings.aws_region)
        state_machine = sfn.create_state_machine(
            name="cwd-test-ingest",
            definition=_STATE_MACHINE_DEFINITION,
            roleArn="arn:aws:iam::111122223333:role/fake-sfn-role",
        )
        monkeypatch.setenv("CWD_INGESTION_STATE_MACHINE_ARN", state_machine["stateMachineArn"])

        # `moto` mocks SQS well (unlike Bedrock/S3 Vectors/AppSync), so handler-level tests use a
        # real (mocked) queue and the real `SqsAnswerQueue` adapter, the same convention already
        # applied above to DynamoDB/S3/Step Functions — `answering` itself is never invoked by
        # these tests, only the enqueue call `api.conversations.post_message` makes.
        sqs = boto3.client("sqs", region_name=settings.aws_region)
        queue = sqs.create_queue(QueueName="cwd-test-answer-queue")
        monkeypatch.setenv("CWD_ANSWER_QUEUE_URL", queue["QueueUrl"])

        deps.get_repo.cache_clear()
        deps.get_store.cache_clear()
        deps.get_workflow.cache_clear()
        deps.get_answer_queue.cache_clear()
        yield
        deps.get_repo.cache_clear()
        deps.get_store.cache_clear()
        deps.get_workflow.cache_clear()
        # A test may have `monkeypatch.setattr(deps, "get_answer_queue", ...)`'d this to a plain
        # callable (e.g. to raise on enqueue) — `monkeypatch` only reverts *after* this fixture's
        # own teardown runs (it finalizes in the reverse of its setup order, and this fixture
        # depends on `monkeypatch`), so the replacement, not the real `lru_cache`-wrapped
        # function, is still in place here.
        if hasattr(deps.get_answer_queue, "cache_clear"):
            deps.get_answer_queue.cache_clear()
