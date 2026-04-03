"""Step Functions adapter for starting an ingestion execution
(docs/03-ingestion.md#state-machine). Used only by `api`'s `/ingest` route — the state machine's
own tasks never call back into this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Execution:
    execution_arn: str


class Workflow:
    def __init__(self, client: Any, *, state_machine_arn: str) -> None:
        self._client = client
        self._state_machine_arn = state_machine_arn

    def start_execution(self, *, name: str, input_payload: dict[str, Any]) -> Execution:
        """`name` must be unique per state machine for 90 days (a Step Functions constraint),
        so callers must never reuse a bare `documentId` across a re-ingest — see
        `api.documents.ingest`, which appends a fresh ULID."""
        response = self._client.start_execution(
            stateMachineArn=self._state_machine_arn,
            name=name,
            input=json.dumps(input_payload),
        )
        return Execution(execution_arn=response["executionArn"])
