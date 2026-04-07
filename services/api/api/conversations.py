"""Conversation and message route handlers' business logic
(docs/05-api-contracts.md#conversations-and-messages).

`post_message` is the interesting one, and it deviates from the documented steady-state
response. docs/05 describes `202 {userMessage, assistantMessageId, channel}` — a placeholder to
subscribe against, because the answer turn runs on an SQS worker. Phase 5 has neither the queue
nor the AppSync channel yet (docs/10-roadmap.md#phase-5: "Synchronous — no SQS worker, no
streaming yet"), so `post_message` invokes the `answering` Lambda synchronously and returns the
*resolved* assistant message in the same response: `201 {userMessage, assistantMessage}`. Phase 6
restores the documented shape without changing anything below `answering.turn.run_turn`.
"""

from __future__ import annotations

from typing import Any

from api import validation
from api.errors import conflict
from common import authz
from common.answering_client import AnsweringInvocationError, AnsweringInvokerProtocol
from common.repo import NotFound, Repo, new_id

# The lock's TTL needs to comfortably outlive a real answer turn (Bedrock generation with
# adaptive thinking can run tens of seconds) while still being reclaimable in reasonable time if
# the answering Lambda dies without reaching its own `finally` (docs/02-data-model.md: "a stale
# lock is reclaimable"). `answering`'s own Lambda timeout (300s, docs/01-architecture.md) is the
# hard ceiling; this is comfortably under it so a stuck lock doesn't outlive the Lambda that
# could have released it.
_LOCK_TTL_SECONDS = 280

_FAILED_INVOCATION_TEXT = "Something went wrong while generating this answer. Please try again."


def create(repo: Repo, owner_sub: str, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
    authz.require_project(repo, owner_sub, project_id)
    title, pinned_document_ids = validation.validate_create_conversation(body)
    conversation = repo.create_conversation(
        project_id=project_id,
        owner_sub=owner_sub,
        title=title,
        pinned_document_ids=pinned_document_ids,
    )
    return conversation.to_api()


def list_for_project(
    repo: Repo, owner_sub: str, project_id: str, query: dict[str, str] | None
) -> dict[str, Any]:
    authz.require_project(repo, owner_sub, project_id)
    limit, cursor = validation.validate_pagination(query)
    items, next_cursor = repo.list_conversations(project_id, limit=limit, cursor=cursor)
    return {"items": [c.to_api() for c in items], "nextCursor": next_cursor}


def get(repo: Repo, owner_sub: str, conversation_id: str) -> dict[str, Any]:
    conversation = authz.require_conversation(repo, owner_sub, conversation_id)
    return conversation.to_api()


def patch(repo: Repo, owner_sub: str, conversation_id: str, body: dict[str, Any]) -> dict[str, Any]:
    authz.require_conversation(repo, owner_sub, conversation_id)
    title, pinned_document_ids = validation.validate_patch_conversation(body)
    conversation = repo.update_conversation(
        conversation_id, title=title, pinned_document_ids=pinned_document_ids
    )
    return conversation.to_api()


def delete(repo: Repo, owner_sub: str, conversation_id: str) -> None:
    conversation = authz.require_conversation(repo, owner_sub, conversation_id)
    repo.delete_messages(conversation_id)
    repo.delete_conversation(conversation)


def list_messages(
    repo: Repo, owner_sub: str, conversation_id: str, query: dict[str, str] | None
) -> dict[str, Any]:
    authz.require_conversation(repo, owner_sub, conversation_id)
    limit, cursor = validation.validate_pagination(query)
    items, next_cursor = repo.list_messages(conversation_id, limit=limit, cursor=cursor)
    return {"items": [m.to_api() for m in items], "nextCursor": next_cursor}


def post_message(
    repo: Repo,
    invoker: AnsweringInvokerProtocol,
    owner_sub: str,
    conversation_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """docs/05-api-contracts.md#conversations-and-messages: validate -> claim the conversation
    lock conditionally (409 `ANSWER_IN_FLIGHT` if already held and unexpired) -> write the user
    message -> run the answer turn -> return both messages."""
    conversation = authz.require_conversation(repo, owner_sub, conversation_id)
    text = validation.validate_post_message(body)

    # History is read *before* the user message below is written, so it never includes the
    # message currently being posted — `rewrite_query` needs prior turns only.
    prior_messages, _ = repo.list_messages(conversation_id, limit=100, cursor=None)
    history = [{"role": m.role, "text": m.text} for m in prior_messages]

    assistant_message_id = new_id()
    if not repo.claim_lock(conversation_id, assistant_message_id, ttl_seconds=_LOCK_TTL_SECONDS):
        raise conflict("ANSWER_IN_FLIGHT", "An answer is already in flight for this conversation.")

    user_message = repo.create_message(
        conversation_id=conversation_id,
        project_id=conversation.project_id,
        owner_sub=owner_sub,
        role="user",
        status="COMPLETE",
        text=text,
    )
    repo.increment_message_count(conversation_id, conversation.project_id, by=1)

    payload = {
        "conversationId": conversation_id,
        "projectId": conversation.project_id,
        "ownerSub": owner_sub,
        "assistantMessageId": assistant_message_id,
        "userText": text,
        "pinnedDocumentIds": conversation.pinned_document_ids,
        "history": history,
    }
    try:
        assistant_message_body = invoker.invoke(payload)
    except AnsweringInvocationError:
        # `answering.turn.run_turn`'s own `finally` releases the lock and persists a `FAILED`
        # message in every ordinary failure mode — this branch only covers the rarer case where
        # the invoke itself failed (throttling, network) or the function crashed before its
        # `finally` ran, so both need a best-effort fallback here too.
        repo.release_lock(conversation_id)
        assistant_message = repo.create_message(
            message_id=assistant_message_id,
            conversation_id=conversation_id,
            project_id=conversation.project_id,
            owner_sub=owner_sub,
            role="assistant",
            status="FAILED",
            text=_FAILED_INVOCATION_TEXT,
        )
        repo.increment_message_count(conversation_id, conversation.project_id, by=1)
        assistant_message_body = assistant_message.to_api()

    return {"userMessage": user_message.to_api(), "assistantMessage": assistant_message_body}


def cancel_message(
    repo: Repo, owner_sub: str, conversation_id: str, message_id: str
) -> dict[str, Any]:
    """docs/05-api-contracts.md: "It is best-effort: an already-completed turn returns 202 and
    does nothing." Phase 5 is fully synchronous (no async worker to interrupt mid-flight), so by
    the time a client could call this the turn has already resolved — this is always that no-op
    case. Phase 6 gives it real teeth (docs/10-roadmap.md#phase-6)."""
    authz.require_conversation(repo, owner_sub, conversation_id)
    if repo.get_message(conversation_id, message_id) is None:
        raise NotFound(message_id)
    return {"status": "accepted"}
