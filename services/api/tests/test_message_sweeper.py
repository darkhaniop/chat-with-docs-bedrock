from __future__ import annotations

from typing import Any

import boto3
import pytest

from api import deps
from api.message_sweeper import lambda_handler
from common.config import get_settings
from common.testing.events import FakeEventsPublisher

_OLD_TIMESTAMP = {"S": "2000-01-01T00:00:00Z"}


def _backdate_created_at(pk: str, sk: str) -> None:
    settings = get_settings()
    client = boto3.client("dynamodb", region_name=settings.aws_region)
    client.update_item(
        TableName=settings.table_name,
        Key={"pk": {"S": pk}, "sk": {"S": sk}},
        UpdateExpression="SET createdAt = :old",
        ExpressionAttributeValues={":old": _OLD_TIMESTAMP},
    )


@pytest.fixture
def fake_events(monkeypatch: pytest.MonkeyPatch) -> FakeEventsPublisher:
    fake = FakeEventsPublisher()
    monkeypatch.setattr(deps, "get_events", lambda: fake)
    return fake


def _seed_conversation_and_stuck_message(repo: Any) -> tuple[str, str]:
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    conversation = repo.create_conversation(
        project_id=project.project_id, owner_sub="user-1", title="", pinned_document_ids=[]
    )
    repo.claim_lock(conversation.conversation_id, "assistant-1", ttl_seconds=6000)
    message = repo.create_message(
        message_id="assistant-1",
        conversation_id=conversation.conversation_id,
        project_id=project.project_id,
        owner_sub="user-1",
        role="assistant",
        status="STREAMING",
        text="",
    )
    _backdate_created_at(f"CONV#{conversation.conversation_id}", f"MSG#{message.message_id}")
    return conversation.conversation_id, message.message_id


def test_marks_a_stuck_streaming_message_failed_and_releases_the_lock(
    fake_events: FakeEventsPublisher,
) -> None:
    repo = deps.get_repo()
    conversation_id, message_id = _seed_conversation_and_stuck_message(repo)

    result = lambda_handler({}, context=None)  # type: ignore[arg-type]

    assert result["sweptCount"] == 1
    message = repo.get_message(conversation_id, message_id)
    assert message is not None
    assert message.status == "FAILED"
    conversation = repo.get_conversation(conversation_id)
    assert conversation is not None
    assert conversation.active_message_id is None
    assert conversation.message_count == 1

    [published] = fake_events.events
    assert published.channel == f"/conversations/{conversation_id}"
    assert published.event_type == "message.failed"
    assert published.data["code"] == "TIMEOUT"


def test_does_not_sweep_a_recent_streaming_message(fake_events: FakeEventsPublisher) -> None:
    repo = deps.get_repo()
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    conversation = repo.create_conversation(
        project_id=project.project_id, owner_sub="user-1", title="", pinned_document_ids=[]
    )
    repo.create_message(
        message_id="assistant-1",
        conversation_id=conversation.conversation_id,
        project_id=project.project_id,
        owner_sub="user-1",
        role="assistant",
        status="STREAMING",
        text="",
    )

    result = lambda_handler({}, context=None)  # type: ignore[arg-type]

    assert result["sweptCount"] == 0
    assert fake_events.events == []


def test_does_not_clobber_a_newer_message_holding_the_lock(
    fake_events: FakeEventsPublisher,
) -> None:
    repo = deps.get_repo()
    conversation_id, _message_id = _seed_conversation_and_stuck_message(repo)
    # A second turn legitimately claimed the lock after the first went stale.
    repo.release_lock(conversation_id)
    repo.claim_lock(conversation_id, "assistant-2", ttl_seconds=6000)

    lambda_handler({}, context=None)  # type: ignore[arg-type]

    conversation = repo.get_conversation(conversation_id)
    assert conversation is not None
    assert conversation.active_message_id == "assistant-2"
