from __future__ import annotations

import json
import os
from typing import Any

import boto3
import pytest

from api import deps
from api.handler import lambda_handler
from common.config import get_settings
from common.testing.answer_queue import FakeAnswerQueue

_CLAIMS = {"sub": "user-1"}
_OTHER_CLAIMS = {"sub": "user-2"}


def _enqueued_payloads() -> list[dict[str, Any]]:
    """Drains the test queue via the real (moto-mocked) SQS client — `api.conversations.
    post_message` enqueues rather than invoking `answering` directly as of Phase 6, so handler
    tests assert against what was *enqueued*, not a resolved assistant message; `answering.turn.
    run_turn`'s own test suite covers what happens after a job is picked up."""
    settings = get_settings()
    sqs = boto3.client("sqs", region_name=settings.aws_region)
    response = sqs.receive_message(
        QueueUrl=os.environ["CWD_ANSWER_QUEUE_URL"], MaxNumberOfMessages=10
    )
    return [json.loads(m["Body"]) for m in response.get("Messages", [])]


def _event(
    route_key: str,
    *,
    path_parameters: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
    claims: dict[str, Any] | None = _CLAIMS,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_context: dict[str, Any] = {"requestId": "req-1"}
    if claims is not None:
        request_context["authorizer"] = {"jwt": {"claims": claims}}
    event: dict[str, Any] = {"routeKey": route_key, "requestContext": request_context}
    if path_parameters is not None:
        event["pathParameters"] = path_parameters
    if query is not None:
        event["queryStringParameters"] = query
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def _call(event: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    response = lambda_handler(event, context=None)  # type: ignore[arg-type]
    body = json.loads(response["body"]) if response.get("body") else {}
    return response["statusCode"], body


def _create_project(name: str = "Ashford") -> dict[str, Any]:
    status, body = _call(_event("POST /projects", body={"name": name}))
    assert status == 201
    return body


def _create_conversation(project_id: str, **kwargs: Any) -> dict[str, Any]:
    status, body = _call(
        _event(
            "POST /projects/{projectId}/conversations",
            path_parameters={"projectId": project_id},
            **kwargs,
        )
    )
    assert status == 201
    return body


def test_create_then_get_conversation() -> None:
    project = _create_project()
    created = _create_conversation(project["projectId"], body={"title": "first chat"})

    assert created["title"] == "first chat"
    assert created["pinnedDocumentIds"] == []
    assert "ownerSub" not in created
    assert "activeMessageId" not in created

    status, body = _call(
        _event(
            "GET /conversations/{conversationId}",
            path_parameters={"conversationId": created["conversationId"]},
        )
    )
    assert status == 200
    assert body["conversationId"] == created["conversationId"]


def test_get_conversation_404s_for_a_different_owner() -> None:
    project = _create_project()
    created = _create_conversation(project["projectId"], body={})

    status, _ = _call(
        _event(
            "GET /conversations/{conversationId}",
            path_parameters={"conversationId": created["conversationId"]},
            claims=_OTHER_CLAIMS,
        )
    )
    assert status == 404


def test_list_conversations_for_project() -> None:
    project = _create_project()
    _create_conversation(project["projectId"], body={"title": "a"})
    _create_conversation(project["projectId"], body={"title": "b"})

    status, body = _call(
        _event(
            "GET /projects/{projectId}/conversations",
            path_parameters={"projectId": project["projectId"]},
        )
    )
    assert status == 200
    assert {c["title"] for c in body["items"]} == {"a", "b"}


def test_patch_conversation_updates_title_and_pinned_documents() -> None:
    project = _create_project()
    created = _create_conversation(project["projectId"], body={"title": "old"})

    status, body = _call(
        _event(
            "PATCH /conversations/{conversationId}",
            path_parameters={"conversationId": created["conversationId"]},
            body={"title": "new", "pinnedDocumentIds": ["doc1"]},
        )
    )
    assert status == 200
    assert body["title"] == "new"
    assert body["pinnedDocumentIds"] == ["doc1"]


def test_delete_conversation_removes_it_and_its_messages() -> None:
    project = _create_project()
    created = _create_conversation(project["projectId"], body={})

    status, _ = _call(
        _event(
            "POST /conversations/{conversationId}/messages",
            path_parameters={"conversationId": created["conversationId"]},
            body={"text": "hello"},
        )
    )
    assert status == 202

    status, body = _call(
        _event(
            "DELETE /conversations/{conversationId}",
            path_parameters={"conversationId": created["conversationId"]},
        )
    )
    assert status == 204
    assert body == {}

    status, _ = _call(
        _event(
            "GET /conversations/{conversationId}",
            path_parameters={"conversationId": created["conversationId"]},
        )
    )
    assert status == 404

    repo = deps.get_repo()
    messages, _ = repo.list_messages(created["conversationId"], limit=10, cursor=None)
    assert messages == []


def test_post_message_writes_user_message_and_enqueues_the_turn() -> None:
    project = _create_project()
    created = _create_conversation(project["projectId"], body={})

    status, body = _call(
        _event(
            "POST /conversations/{conversationId}/messages",
            path_parameters={"conversationId": created["conversationId"]},
            body={"text": "What was Q3 uptime?"},
        )
    )

    assert status == 202
    assert body["userMessage"]["role"] == "user"
    assert body["userMessage"]["text"] == "What was Q3 uptime?"
    assert body["assistantMessageId"]
    assert body["channel"] == f"/conversations/{created['conversationId']}"

    # The lock is claimed (not released) while the turn is queued — `answering.turn.run_turn`
    # releases it once a real worker picks the job up, which no handler-level test simulates.
    conversation = deps.get_repo().get_conversation(created["conversationId"])
    assert conversation is not None
    assert conversation.active_message_id == body["assistantMessageId"]

    placeholder = deps.get_repo().get_message(created["conversationId"], body["assistantMessageId"])
    assert placeholder is not None
    assert placeholder.status == "STREAMING"

    [call] = _enqueued_payloads()
    assert call["conversationId"] == created["conversationId"]
    assert call["userText"] == "What was Q3 uptime?"
    assert call["history"] == []


def test_post_message_rejects_empty_text() -> None:
    project = _create_project()
    created = _create_conversation(project["projectId"], body={})

    status, body = _call(
        _event(
            "POST /conversations/{conversationId}/messages",
            path_parameters={"conversationId": created["conversationId"]},
            body={"text": ""},
        )
    )
    assert status == 400
    assert body["error"]["code"] == "VALIDATION_ERROR"


def test_post_message_returns_409_when_a_lock_is_already_held() -> None:
    project = _create_project()
    created = _create_conversation(project["projectId"], body={})
    conversation_id = created["conversationId"]

    repo = deps.get_repo()
    assert repo.claim_lock(conversation_id, "some-other-message-id", ttl_seconds=60)

    status, body = _call(
        _event(
            "POST /conversations/{conversationId}/messages",
            path_parameters={"conversationId": conversation_id},
            body={"text": "hello"},
        )
    )
    assert status == 409
    assert body["error"]["code"] == "ANSWER_IN_FLIGHT"


def test_post_message_propagates_when_the_enqueue_itself_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`AnswerQueueProtocol.enqueue` raising (throttling, network) is not caught by
    `post_message` — unlike Phase 5's synchronous invoke, there is no resolved-or-failed message
    to fall back to before a worker has even picked the job up, so this propagates the same way
    any other unexpected exception in a handler would, rather than a fabricated FAILED message.
    The lock stays claimed; the stuck-message sweeper is the eventual backstop."""
    fake = FakeAnswerQueue(raises=True)
    monkeypatch.setattr(deps, "get_answer_queue", lambda: fake)

    project = _create_project()
    created = _create_conversation(project["projectId"], body={})

    with pytest.raises(RuntimeError):
        _call(
            _event(
                "POST /conversations/{conversationId}/messages",
                path_parameters={"conversationId": created["conversationId"]},
                body={"text": "hello"},
            )
        )


def test_second_message_sees_the_first_in_history() -> None:
    project = _create_project()
    created = _create_conversation(project["projectId"], body={})
    conversation_id = created["conversationId"]

    _call(
        _event(
            "POST /conversations/{conversationId}/messages",
            path_parameters={"conversationId": conversation_id},
            body={"text": "first"},
        )
    )
    # The first turn's lock is never released by a real worker in this test, so the second post
    # would 409 — release it manually to simulate the first turn having completed.
    deps.get_repo().release_lock(conversation_id)
    _call(
        _event(
            "POST /conversations/{conversationId}/messages",
            path_parameters={"conversationId": conversation_id},
            body={"text": "second"},
        )
    )

    second_call = _enqueued_payloads()[-1]
    roles_and_texts = [(h["role"], h["text"]) for h in second_call["history"]]
    # The first turn's placeholder assistant message (empty text, STREAMING) is included too —
    # unlike Phase 5, there's no resolved "assistant" turn to see in history until a worker runs.
    assert ("user", "first") in roles_and_texts
    assert ("assistant", "") in roles_and_texts


def test_cancel_message_flips_the_flag_on_a_streaming_message() -> None:
    project = _create_project()
    created = _create_conversation(project["projectId"], body={})
    status, posted = _call(
        _event(
            "POST /conversations/{conversationId}/messages",
            path_parameters={"conversationId": created["conversationId"]},
            body={"text": "hello"},
        )
    )
    message_id = posted["assistantMessageId"]

    status, body = _call(
        _event(
            "POST /conversations/{conversationId}/messages/{messageId}/cancel",
            path_parameters={"conversationId": created["conversationId"], "messageId": message_id},
        )
    )
    assert status == 202
    assert body["status"] == "accepted"

    message = deps.get_repo().get_message(created["conversationId"], message_id)
    assert message is not None
    assert message.cancel_requested is True


def test_cancel_message_is_a_no_op_202_once_terminal() -> None:
    project = _create_project()
    created = _create_conversation(project["projectId"], body={})
    status, posted = _call(
        _event(
            "POST /conversations/{conversationId}/messages",
            path_parameters={"conversationId": created["conversationId"]},
            body={"text": "hello"},
        )
    )
    message_id = posted["assistantMessageId"]
    deps.get_repo().create_message(
        message_id=message_id,
        conversation_id=created["conversationId"],
        project_id=project["projectId"],
        owner_sub=_CLAIMS["sub"],
        role="assistant",
        status="COMPLETE",
        text="done",
    )

    status, body = _call(
        _event(
            "POST /conversations/{conversationId}/messages/{messageId}/cancel",
            path_parameters={"conversationId": created["conversationId"], "messageId": message_id},
        )
    )
    assert status == 202
    assert body["status"] == "accepted"

    message = deps.get_repo().get_message(created["conversationId"], message_id)
    assert message is not None
    assert message.cancel_requested is False


def test_cancel_message_404s_for_an_unknown_message_id() -> None:
    project = _create_project()
    created = _create_conversation(project["projectId"], body={})

    status, _ = _call(
        _event(
            "POST /conversations/{conversationId}/messages/{messageId}/cancel",
            path_parameters={"conversationId": created["conversationId"], "messageId": "nope"},
        )
    )
    assert status == 404


@pytest.mark.parametrize("body", [None, {}])
def test_create_conversation_defaults_title_and_pinned_documents(
    body: dict[str, Any] | None,
) -> None:
    project = _create_project()
    created = _create_conversation(project["projectId"], body=body)
    assert created["title"] == ""
    assert created["pinnedDocumentIds"] == []
