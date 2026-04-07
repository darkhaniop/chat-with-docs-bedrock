"""moto-backed AWS for handler-level routing tests. Uses the same table/bucket-naming
convention `common.config.get_settings()` already resolves to by default (env `dev`), so the
Lambda's own dependency wiring (`api.deps`) needs no test-only env override beyond the bucket
name, which is genuinely deploy-time-injected (see `api/deps.py`)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from api import deps
from common.config import get_settings
from common.testing.answering import FakeAnsweringInvoker
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


def _simulate_answering_lambda(payload: dict[str, Any]) -> dict[str, Any]:
    """The default responder mirrors what `answering.turn.run_turn` actually does — persist the
    assistant message, bump the conversation's message count, release the lock — so
    handler-level tests can assert on the *effects* of a real invocation (lock released, message
    findable, next turn's history includes it) without needing Bedrock/S3 Vectors fakes just to
    exercise `api`'s own orchestration."""
    repo = deps.get_repo()
    message = repo.create_message(
        message_id=payload["assistantMessageId"],
        conversation_id=payload["conversationId"],
        project_id=payload["projectId"],
        owner_sub=payload["ownerSub"],
        role="assistant",
        status="COMPLETE",
        text="fake answer",
    )
    repo.increment_message_count(payload["conversationId"], payload["projectId"], by=1)
    repo.release_lock(payload["conversationId"])
    return message.to_api()


@pytest.fixture
def fake_answering_invoker(monkeypatch: pytest.MonkeyPatch) -> FakeAnsweringInvoker:
    """`api` never calls Bedrock directly (docs/07-security.md#iam) — it invokes the `answering`
    Lambda, which handler-level tests replace with this scriptable fake rather than a real
    `lambda:Invoke` (moto doesn't run the invoked function's code anyway)."""
    fake = FakeAnsweringInvoker(responder=_simulate_answering_lambda)
    monkeypatch.setattr(deps, "get_answering_invoker", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def aws_stack(
    monkeypatch: pytest.MonkeyPatch,
    fake_vector_index: FakeVectorIndex,
    fake_answering_invoker: FakeAnsweringInvoker,
) -> Iterator[None]:
    monkeypatch.setenv("CWD_DOCUMENTS_BUCKET_NAME", _BUCKET_NAME)
    monkeypatch.setenv("CWD_VECTOR_BUCKET_NAME", _VECTOR_BUCKET_NAME)
    monkeypatch.setenv("CWD_ANSWERING_FUNCTION_NAME", "cwd-test-answering")
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

        deps.get_repo.cache_clear()
        deps.get_store.cache_clear()
        deps.get_workflow.cache_clear()
        yield
        deps.get_repo.cache_clear()
        deps.get_store.cache_clear()
        deps.get_workflow.cache_clear()
