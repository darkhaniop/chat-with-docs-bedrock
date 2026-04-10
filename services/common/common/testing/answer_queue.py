"""`FakeAnswerQueue` — a scriptable stand-in for `common.answer_queue.AnswerQueueProtocol` in
`api`'s own unit tests, which exercise the lock/placeholder-write/enqueue orchestration in
`api.conversations.post_message` without a real SQS queue (the answer turn itself is
`services/answering`'s own test suite's job, unchanged from Phase 5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeAnswerQueue:
    raises: bool = False
    enqueued: list[dict[str, Any]] = field(default_factory=list)

    def enqueue(self, payload: dict[str, Any]) -> None:
        if self.raises:
            raise RuntimeError("simulated enqueue failure")
        self.enqueued.append(payload)
