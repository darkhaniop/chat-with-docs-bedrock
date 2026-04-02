"""AppSync Events publish adapter (docs/05-api-contracts.md#appsync-events).

Publish-only, per docs/10-roadmap.md Phase 3 task 6: the AppSync Events API itself is
`CwdDevRealtimeStack`, built in Phase 6 — this phase only needs somewhere to *send* progress
events so ingestion isn't silently mute; the SPA doesn't consume them until Phase 6 wires a
subscriber. Until that stack exists, `Settings.events_http_domain` is unset and `publish` is a
no-op logged at INFO. Once Phase 6 deploys the real domain, no code here changes — only the
environment variable.

Uses `urllib`/`botocore.auth.SigV4Auth` directly rather than adding an HTTP client dependency —
AppSync Events' data-plane publish API is a single signed `POST`, not worth a new library.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import UTC, datetime
from typing import Any, Protocol

import boto3
from aws_lambda_powertools import Logger
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

logger = Logger(service="events", child=True)


class EventsPublisher(Protocol):
    def publish(self, channel: str, event_type: str, data: dict[str, Any], *, seq: int) -> None: ...


def _envelope(event_type: str, data: dict[str, Any], *, seq: int) -> dict[str, Any]:
    """docs/05-api-contracts.md#appsync-events: every event carries `{type, at, seq, data}`."""
    now = datetime.now(UTC)
    return {
        "type": event_type,
        "at": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
        "seq": seq,
        "data": data,
    }


class AppSyncEventsPublisher:
    def __init__(self, *, domain: str | None, region: str) -> None:
        self._domain = domain
        self._region = region
        self._session = boto3.Session()

    def publish(self, channel: str, event_type: str, data: dict[str, Any], *, seq: int) -> None:
        if self._domain is None:
            logger.info(
                "event publish skipped: no realtime stack deployed yet",
                eventType=event_type,
                channel=channel,
            )
            return
        body = json.dumps(
            {"channel": channel, "events": [json.dumps(_envelope(event_type, data, seq=seq))]}
        ).encode()
        request = AWSRequest(
            method="POST",
            url=f"https://{self._domain}/event",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        credentials = self._session.get_credentials()
        SigV4Auth(credentials, "appsync", self._region).add_auth(request)
        prepared = request.prepare()
        http_request = urllib.request.Request(  # noqa: S310 — internal AWS endpoint, not user input
            prepared.url, data=prepared.body, headers=dict(prepared.headers), method="POST"
        )
        with urllib.request.urlopen(http_request, timeout=5) as response:  # noqa: S310
            if response.status >= 300:
                logger.warning("event publish failed", status=response.status, eventType=event_type)
