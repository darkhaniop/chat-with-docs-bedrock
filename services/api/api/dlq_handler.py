"""DLQ-triggered Lambda (docs/04-retrieval-and-citations.md#failure-behaviour: "SQS message
fails twice -> DLQ; alarm; message marked FAILED by the DLQ handler").

Only reached when the `answering` Lambda's invocation itself failed twice — genuinely unexpected
crashes (OOM, an unhandled bug before `run_turn`'s own try/except could run), since `run_turn`
already resolves every "expected" failure to a persisted terminal message and never re-raises
(docs/answering/turn.py). The DLQ's message body is the original, unmodified answer-turn payload
`api.conversations.post_message` enqueued (SQS redelivers it as-is), so this needs no new payload
shape — just enough of it to know which message to fail and which lock to release.
"""

from __future__ import annotations

import json
from typing import Any

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

from api import deps

logger = Logger(service="answering-dlq-handler")

_FAILED_TEXT = "Something went wrong while generating this answer. Please try again."


def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    repo = deps.get_repo()
    events = deps.get_events()

    for record in event["Records"]:
        payload = json.loads(record["body"])
        conversation_id = payload["conversationId"]
        message_id = payload["assistantMessageId"]
        logger.append_keys(conversationId=conversation_id, assistantMessageId=message_id)
        logger.warning("answer turn reached the DLQ; marking it failed")

        repo.create_message(
            message_id=message_id,
            conversation_id=conversation_id,
            project_id=payload["projectId"],
            owner_sub=payload["ownerSub"],
            role="assistant",
            status="FAILED",
            text=_FAILED_TEXT,
        )
        # See `api/message_sweeper.py`'s matching comment: this finalizes the placeholder
        # `post_message` wrote without counting, so it increments exactly once here.
        repo.increment_message_count(conversation_id, payload["projectId"], by=1)
        repo.release_lock_if_holder(conversation_id, message_id)
        events.publish(
            f"/conversations/{conversation_id}",
            "message.failed",
            {"messageId": message_id, "code": "INTERNAL_ERROR", "message": _FAILED_TEXT},
            seq=1,
        )

    return {"processed": len(event["Records"])}
