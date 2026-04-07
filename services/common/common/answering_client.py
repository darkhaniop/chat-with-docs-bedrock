"""Lambda-to-Lambda invoke adapter, `api` -> `answering` (docs/01-architecture.md#answering).

`api` must never hold Bedrock permissions (docs/07-security.md#iam), so it cannot run the answer
turn in-process — it invokes the `answering` Lambda synchronously (`RequestResponse`) instead.
This is a Phase 5 stand-in for the documented steady state: docs/10-roadmap.md's Phase 5 goal is
explicitly "no SQS worker, no streaming yet," and Phase 6 replaces this adapter's one call site
(`api.conversations.post_message`) with an SQS `SendMessage` without touching `answering.turn`.
"""

from __future__ import annotations

import json
from typing import Any, Protocol


class AnsweringInvocationError(Exception):
    """Raised when the invoke itself failed (throttling, network) or the function raised an
    unhandled exception (a `FunctionError` in the response) — distinct from the function
    returning a normal result, which `answering.turn.run_turn` guarantees always represents a
    resolved (COMPLETE/BLOCKED/FAILED) message, never a propagated exception."""


class AnsweringInvokerProtocol(Protocol):
    def invoke(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class AnsweringInvoker:
    def __init__(self, client: Any, *, function_name: str) -> None:
        self._client = client
        self._function_name = function_name

    def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client.invoke(
            FunctionName=self._function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode(),
        )
        if response.get("FunctionError"):
            raise AnsweringInvocationError(response["FunctionError"])
        result: dict[str, Any] = json.loads(response["Payload"].read())
        return result
