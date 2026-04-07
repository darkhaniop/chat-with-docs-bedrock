"""`FakeAnsweringInvoker` — a scriptable stand-in for `common.answering_client.AnsweringInvoker`
in `api`'s own unit tests, which exercise the lock/persist/error-handling orchestration in
`api.conversations.post_message` without running the real answer turn (that's
`services/answering`'s own test suite's job)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from common.answering_client import AnsweringInvocationError


@dataclass
class FakeAnsweringInvoker:
    responder: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    raises: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)

    def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        if self.raises:
            raise AnsweringInvocationError("simulated invocation failure")
        if self.responder is not None:
            return self.responder(payload)
        return {
            "messageId": payload["assistantMessageId"],
            "role": "assistant",
            "status": "COMPLETE",
            "text": "fake answer",
            "citations": [],
            "retrieved": [],
        }
