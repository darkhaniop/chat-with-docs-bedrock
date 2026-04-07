"""`answering` Lambda entry point (docs/01-architecture.md#answering).

Synchronous for Phase 5 (docs/10-roadmap.md's Phase 5 goal: "no SQS worker, no streaming yet") —
invoked directly (`RequestResponse`) by `api`'s `POST .../messages` route, since `api` must never
hold Bedrock permissions (docs/07-security.md#iam). Phase 6 swaps the trigger for SQS without
touching `turn.run_turn`.
"""

from __future__ import annotations

from typing import Any

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

from answering import deps
from answering.turn import run_turn
from common.config import get_settings

logger = Logger(service="answering")


def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    conversation_id = event["conversationId"]
    # Whitelisted fields only (docs/07-security.md#data-protection) — never `userText`.
    logger.append_keys(
        requestId=context.aws_request_id,
        conversationId=conversation_id,
        assistantMessageId=event["assistantMessageId"],
    )

    settings = get_settings()
    history_raw = event.get("history") or []
    message = run_turn(
        repo=deps.get_repo(),
        store=deps.get_store(),
        vector_index=deps.get_vector_index(),
        nova=deps.get_nova(),
        bedrock=deps.get_bedrock(),
        guardrail=deps.get_guardrail(),
        settings=settings,
        conversation_id=conversation_id,
        project_id=event["projectId"],
        owner_sub=event["ownerSub"],
        assistant_message_id=event["assistantMessageId"],
        user_text=event["userText"],
        pinned_document_ids=event.get("pinnedDocumentIds") or [],
        history=[(turn["role"], turn["text"]) for turn in history_raw],
    )
    return message.to_api()
