from __future__ import annotations

import json
from typing import Any

import pytest

from api import deps
from api.dlq_handler import lambda_handler
from common.testing.events import FakeEventsPublisher


@pytest.fixture
def fake_events(monkeypatch: pytest.MonkeyPatch) -> FakeEventsPublisher:
    fake = FakeEventsPublisher()
    monkeypatch.setattr(deps, "get_events", lambda: fake)
    return fake


def _sqs_event(payload: dict[str, Any]) -> dict[str, Any]:
    return {"Records": [{"body": json.dumps(payload)}]}


def test_marks_the_message_failed_and_releases_the_lock(fake_events: FakeEventsPublisher) -> None:
    repo = deps.get_repo()
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    conversation = repo.create_conversation(
        project_id=project.project_id, owner_sub="user-1", title="", pinned_document_ids=[]
    )
    repo.claim_lock(conversation.conversation_id, "assistant-1", ttl_seconds=6000)
    repo.create_message(
        message_id="assistant-1",
        conversation_id=conversation.conversation_id,
        project_id=project.project_id,
        owner_sub="user-1",
        role="assistant",
        status="STREAMING",
        text="",
    )

    payload = {
        "conversationId": conversation.conversation_id,
        "projectId": project.project_id,
        "ownerSub": "user-1",
        "assistantMessageId": "assistant-1",
        "userText": "hello",
        "pinnedDocumentIds": [],
        "history": [],
    }
    result = lambda_handler(_sqs_event(payload), context=None)  # type: ignore[arg-type]

    assert result["processed"] == 1
    message = repo.get_message(conversation.conversation_id, "assistant-1")
    assert message is not None
    assert message.status == "FAILED"
    updated_conversation = repo.get_conversation(conversation.conversation_id)
    assert updated_conversation is not None
    assert updated_conversation.active_message_id is None
    assert updated_conversation.message_count == 1

    [published] = fake_events.events
    assert published.channel == f"/conversations/{conversation.conversation_id}"
    assert published.event_type == "message.failed"
    assert published.data["code"] == "INTERNAL_ERROR"


def test_does_not_clobber_a_newer_message_holding_the_lock(
    fake_events: FakeEventsPublisher,
) -> None:
    repo = deps.get_repo()
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    conversation = repo.create_conversation(
        project_id=project.project_id, owner_sub="user-1", title="", pinned_document_ids=[]
    )
    repo.claim_lock(conversation.conversation_id, "assistant-1", ttl_seconds=6000)
    repo.release_lock(conversation.conversation_id)
    repo.claim_lock(conversation.conversation_id, "assistant-2", ttl_seconds=6000)

    payload = {
        "conversationId": conversation.conversation_id,
        "projectId": project.project_id,
        "ownerSub": "user-1",
        "assistantMessageId": "assistant-1",
        "userText": "hello",
        "pinnedDocumentIds": [],
        "history": [],
    }
    lambda_handler(_sqs_event(payload), context=None)  # type: ignore[arg-type]

    updated_conversation = repo.get_conversation(conversation.conversation_id)
    assert updated_conversation is not None
    assert updated_conversation.active_message_id == "assistant-2"
