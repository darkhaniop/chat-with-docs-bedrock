from __future__ import annotations

import json
from typing import Any

import pytest

from api import deps
from api.handler import lambda_handler
from common.testing.answering import FakeAnsweringInvoker

_CLAIMS = {"sub": "user-1"}
_OTHER_CLAIMS = {"sub": "user-2"}


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


def test_delete_conversation_removes_it_and_its_messages(
    fake_answering_invoker: FakeAnsweringInvoker,
) -> None:
    project = _create_project()
    created = _create_conversation(project["projectId"], body={})

    status, _ = _call(
        _event(
            "POST /conversations/{conversationId}/messages",
            path_parameters={"conversationId": created["conversationId"]},
            body={"text": "hello"},
        )
    )
    assert status == 201

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


def test_post_message_writes_user_message_and_returns_the_resolved_assistant_message(
    fake_answering_invoker: FakeAnsweringInvoker,
) -> None:
    project = _create_project()
    created = _create_conversation(project["projectId"], body={})

    status, body = _call(
        _event(
            "POST /conversations/{conversationId}/messages",
            path_parameters={"conversationId": created["conversationId"]},
            body={"text": "What was Q3 uptime?"},
        )
    )

    assert status == 201
    assert body["userMessage"]["role"] == "user"
    assert body["userMessage"]["text"] == "What was Q3 uptime?"
    assert body["assistantMessage"]["status"] == "COMPLETE"
    assert body["assistantMessage"]["text"] == "fake answer"

    # The lock must be released once the (fake, synchronous) turn "completes".
    conversation = deps.get_repo().get_conversation(created["conversationId"])
    assert conversation is not None
    assert conversation.active_message_id is None

    [call] = fake_answering_invoker.calls
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


def test_post_message_falls_back_to_a_failed_message_when_the_invoke_itself_fails(
    fake_answering_invoker: FakeAnsweringInvoker,
) -> None:
    fake_answering_invoker.raises = True
    project = _create_project()
    created = _create_conversation(project["projectId"], body={})

    status, body = _call(
        _event(
            "POST /conversations/{conversationId}/messages",
            path_parameters={"conversationId": created["conversationId"]},
            body={"text": "hello"},
        )
    )

    assert status == 201
    assert body["assistantMessage"]["status"] == "FAILED"

    conversation = deps.get_repo().get_conversation(created["conversationId"])
    assert conversation is not None
    assert conversation.active_message_id is None


def test_second_message_sees_the_first_in_history(
    fake_answering_invoker: FakeAnsweringInvoker,
) -> None:
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
    _call(
        _event(
            "POST /conversations/{conversationId}/messages",
            path_parameters={"conversationId": conversation_id},
            body={"text": "second"},
        )
    )

    second_call = fake_answering_invoker.calls[-1]
    roles_and_texts = [(h["role"], h["text"]) for h in second_call["history"]]
    assert ("user", "first") in roles_and_texts
    assert ("assistant", "fake answer") in roles_and_texts


def test_cancel_message_is_a_no_op_202(fake_answering_invoker: FakeAnsweringInvoker) -> None:
    project = _create_project()
    created = _create_conversation(project["projectId"], body={})
    status, posted = _call(
        _event(
            "POST /conversations/{conversationId}/messages",
            path_parameters={"conversationId": created["conversationId"]},
            body={"text": "hello"},
        )
    )
    message_id = posted["assistantMessage"]["messageId"]

    status, body = _call(
        _event(
            "POST /conversations/{conversationId}/messages/{messageId}/cancel",
            path_parameters={"conversationId": created["conversationId"], "messageId": message_id},
        )
    )
    assert status == 202
    assert body["status"] == "accepted"


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
