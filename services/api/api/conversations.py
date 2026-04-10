"""Conversation and message route handlers' business logic
(docs/05-api-contracts.md#conversations-and-messages).

`post_message` is the documented steady-state shape as of Phase 6: `202
{userMessage, assistantMessageId, channel}`. Phase 5's synchronous stand-in (invoke `answering`
directly, return the resolved message) is gone — the SQS queue and the `CwdDevRealtimeStack`
channel it names both exist now, so this only writes the user message and a `STREAMING`
placeholder assistant message, enqueues one job, and returns. `answering.turn.run_turn` (invoked
off the queue instead of synchronously) does the actual work, unchanged.
"""

from __future__ import annotations

from typing import Any

from api import validation
from api.errors import conflict
from common import authz
from common.answer_queue import AnswerQueueProtocol
from common.repo import NotFound, Repo, new_id

# The lock's TTL needs to comfortably outlive a real answer turn (Bedrock generation with
# adaptive thinking can run tens of seconds) while still being reclaimable in reasonable time if
# the answering Lambda dies without reaching its own `finally` (docs/02-data-model.md: "a stale
# lock is reclaimable"). `answering`'s own Lambda timeout (300s, docs/01-architecture.md) is the
# hard ceiling; this is comfortably under it so a stuck lock doesn't outlive the Lambda that
# could have released it. The stuck-message sweeper (10 minutes, docs/04's failure table) is the
# backstop if even that isn't reached.
_LOCK_TTL_SECONDS = 280


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
    queue: AnswerQueueProtocol,
    owner_sub: str,
    conversation_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """docs/05-api-contracts.md#conversations-and-messages: validate -> claim the conversation
    lock conditionally (409 `ANSWER_IN_FLIGHT` if already held and unexpired) -> write the user
    message and a `STREAMING` placeholder assistant message -> enqueue -> return `202`."""
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

    # The placeholder (docs/05: "Writes the user message and a placeholder assistant message
    # (status: STREAMING)") gives `GET .../messages` and the stuck-message sweeper something to
    # find immediately, before `answering` ever picks the job off the queue. `answering.turn.
    # run_turn`'s own `_persist` overwrites this same item (same `message_id`) with the resolved
    # content — a `PutItem` replaces wholesale, so no separate "update" path is needed.
    repo.create_message(
        message_id=assistant_message_id,
        conversation_id=conversation_id,
        project_id=conversation.project_id,
        owner_sub=owner_sub,
        role="assistant",
        status="STREAMING",
        text="",
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
    queue.enqueue(payload)

    return {
        "userMessage": user_message.to_api(),
        "assistantMessageId": assistant_message_id,
        "channel": f"/conversations/{conversation_id}",
    }


def cancel_message(
    repo: Repo, owner_sub: str, conversation_id: str, message_id: str
) -> dict[str, Any]:
    """docs/05-api-contracts.md: "It is best-effort: an already-completed turn returns 202 and
    does nothing." `repo.request_cancel` is the real teeth (docs/10-roadmap.md#phase-6): it flips
    `cancelRequested` only if the message is still `STREAMING`, which `answering.stream`'s
    generation loop polls between chunks. Either outcome (flag set, or already-terminal no-op)
    returns the same `202` — the caller has no way to distinguish them and shouldn't need to."""
    authz.require_conversation(repo, owner_sub, conversation_id)
    if repo.get_message(conversation_id, message_id) is None:
        raise NotFound(message_id)
    repo.request_cancel(conversation_id, message_id)
    return {"status": "accepted"}
