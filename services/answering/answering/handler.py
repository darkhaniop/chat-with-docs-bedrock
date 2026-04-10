"""`answering` Lambda entry point (docs/01-architecture.md#answering-sqs--answering-lambda).

Phase 6: triggered by the SQS answer queue (`batchSize: 1` in `infra/lib/compute-stack.ts`, so
one invocation is always exactly one answer turn), replacing Phase 5's synchronous
`RequestResponse` invoke from `api`. `run_turn` itself always resolves to a persisted message and
never raises for an "expected" failure (docs/answering/turn.py) — an exception escaping this
handler is therefore a genuinely unexpected error, which is exactly what should reach SQS's
redrive/DLQ path (docs/04-retrieval-and-citations.md#failure-behaviour) rather than being
swallowed here.
"""

from __future__ import annotations

import json
from typing import Any

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

from answering import deps
from answering.turn import run_turn
from common.config import get_settings

logger = Logger(service="answering")


def lambda_handler(event: dict[str, Any], context: LambdaContext) -> None:
    [record] = event["Records"]  # batchSize=1 — see infra/lib/compute-stack.ts
    payload = json.loads(record["body"])

    conversation_id = payload["conversationId"]
    # Whitelisted fields only (docs/07-security.md#data-protection) — never `userText`.
    logger.append_keys(
        requestId=context.aws_request_id,
        conversationId=conversation_id,
        assistantMessageId=payload["assistantMessageId"],
    )

    settings = get_settings()
    history_raw = payload.get("history") or []
    run_turn(
        repo=deps.get_repo(),
        store=deps.get_store(),
        vector_index=deps.get_vector_index(),
        nova=deps.get_nova(),
        bedrock=deps.get_bedrock(),
        guardrail=deps.get_guardrail(),
        events=deps.get_events(),
        settings=settings,
        conversation_id=conversation_id,
        project_id=payload["projectId"],
        owner_sub=payload["ownerSub"],
        assistant_message_id=payload["assistantMessageId"],
        user_text=payload["userText"],
        pinned_document_ids=payload.get("pinnedDocumentIds") or [],
        history=[(turn["role"], turn["text"]) for turn in history_raw],
    )
