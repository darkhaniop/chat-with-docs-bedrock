"""Scheduled Lambda (docs/04-retrieval-and-citations.md#failure-behaviour): "Lambda times out
(300s) -> Message left STREAMING; a sweeper marks messages stuck > 10 minutes as FAILED." Shares
the `api` Docker image (`services/Dockerfile`) with its own CMD, role, and function — the same
"one image, one dependency set, its own function per handler" convention
`services/api/api/sweeper.py` already established for the orphan-upload sweeper.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

from api import deps
from common.config import get_settings

logger = Logger(service="message-sweeper")

_FAILED_TEXT = "This answer took too long and was stopped."


def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.stuck_message_threshold_minutes)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    repo = deps.get_repo()
    events = deps.get_events()
    stuck = repo.scan_stuck_streaming_messages(older_than_iso=cutoff_iso)

    for message in stuck:
        repo.create_message(
            message_id=message.message_id,
            conversation_id=message.conversation_id,
            project_id=message.project_id,
            owner_sub=message.owner_sub,
            role="assistant",
            status="FAILED",
            text=_FAILED_TEXT,
        )
        # This *finalizes* the placeholder `api.conversations.post_message` wrote without
        # counting it (see that function's comment) — exactly one of "the sweeper", "the DLQ
        # handler", or `answering.turn.run_turn`'s own `_persist` ever reaches this message, so
        # exactly one of them increments it.
        repo.increment_message_count(message.conversation_id, message.project_id, by=1)
        # Not `release_lock` — a newer message may have already claimed the lock legitimately by
        # the time the sweeper gets to this one (docs/common/repo.py's `release_lock_if_holder`).
        repo.release_lock_if_holder(message.conversation_id, message.message_id)
        events.publish(
            f"/conversations/{message.conversation_id}",
            "message.failed",
            {"messageId": message.message_id, "code": "TIMEOUT", "message": _FAILED_TEXT},
            seq=1,
        )

    logger.info("stuck-message sweep complete", sweptCount=len(stuck), cutoff=cutoff_iso)
    return {"sweptCount": len(stuck)}
