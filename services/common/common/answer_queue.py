"""SQS answer-queue adapter, `api` -> `answering` (docs/01-architecture.md#answering-sqs--
answering-lambda).

Phase 6 replaces Phase 5's synchronous `lambda:invoke` stand-in
(`common.answering_client.AnsweringInvoker`, removed on this branch) with the documented steady
state: `api.conversations.post_message` enqueues one job and returns `202` immediately;
`answering`'s own Lambda is triggered by the queue (`services/answering/answering/handler.py`),
not called directly. `api` still never touches Bedrock — enqueueing needs only
`sqs:SendMessage`.
"""

from __future__ import annotations

import json
from typing import Any, Protocol


class AnswerQueueProtocol(Protocol):
    def enqueue(self, payload: dict[str, Any]) -> None: ...


class SqsAnswerQueue:
    def __init__(self, client: Any, *, queue_url: str) -> None:
        self._client = client
        self._queue_url = queue_url

    def enqueue(self, payload: dict[str, Any]) -> None:
        self._client.send_message(QueueUrl=self._queue_url, MessageBody=json.dumps(payload))
