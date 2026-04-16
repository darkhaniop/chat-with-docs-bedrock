"""`test_channel_authz`: Runs against the real deployed `dev` stack's AppSync Events API -
`CWD_INTEGRATION=1 uv run pytest -m integration` (needs `CWD_EVENTS_REALTIME_DOMAIN`/
`CWD_EVENTS_HTTP_DOMAIN`, see `conftest.py`).

Speaks the raw AppSync Events WebSocket protocol directly via `httpx-ws`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast

import httpx_ws
import pytest

if TYPE_CHECKING:
    from conftest import CognitoConfig, EventsConfig

pytestmark = pytest.mark.integration

_TIMEOUT_SECONDS = 10.0


def _request(
    method: str, url: str, *, token: str, body: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as response:  # noqa: S310
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _base64url(payload: dict[str, str]) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


async def _recv_ignoring_keepalives(ws: httpx_ws.AsyncWebSocketSession) -> dict[str, Any]:
    """AWS's own protocol docs: "AWS AppSync sends a keep-alive message to the client to
    maintain the connection" (`{"type": "ka"}`) — periodically and asynchronously with respect
    to any in-flight request, so a plain single `receive_text()` after sending a
    `subscribe`/`publish` can race one and get the keep-alive instead of the actual response.
    Found live this session (a `ka` frame landed between the `subscribe` send and its
    `subscribe_success`)."""
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("timed out waiting for a non-keep-alive message")
        message = json.loads(await ws.receive_text(timeout=remaining))
        if message.get("type") != "ka":
            return cast(dict[str, Any], message)


@asynccontextmanager
async def _connect(
    events_config: EventsConfig, *, id_token: str
) -> AsyncIterator[httpx_ws.AsyncWebSocketSession]:
    header = _base64url({"Authorization": id_token, "host": events_config.http_domain})
    async with httpx_ws.aconnect_ws(
        f"wss://{events_config.realtime_domain}/event/realtime",
        subprotocols=["aws-appsync-event-ws", f"header-{header}"],
        session_class=httpx_ws.AsyncWebSocketSession,
    ) as ws:
        await ws.send_text(json.dumps({"type": "connection_init"}))
        ack = await _recv_ignoring_keepalives(ws)
        assert ack["type"] == "connection_ack", ack
        yield ws


async def _subscribe(
    ws: httpx_ws.AsyncWebSocketSession, *, channel: str, id_token: str, http_domain: str
) -> dict[str, Any]:
    sub_id = str(uuid.uuid4())
    await ws.send_text(
        json.dumps(
            {
                "type": "subscribe",
                "id": sub_id,
                "channel": channel,
                "authorization": {"Authorization": id_token, "host": http_domain},
            }
        )
    )
    return await _recv_ignoring_keepalives(ws)


async def _publish(
    ws: httpx_ws.AsyncWebSocketSession, *, channel: str, id_token: str, http_domain: str
) -> dict[str, Any]:
    pub_id = str(uuid.uuid4())
    await ws.send_text(
        json.dumps(
            {
                "type": "publish",
                "id": pub_id,
                "channel": channel,
                "events": [json.dumps({"probe": "cwd-integration-test"})],
                "authorization": {"Authorization": id_token, "host": http_domain},
            }
        )
    )
    return await _recv_ignoring_keepalives(ws)


def _create_conversation(cognito_config: CognitoConfig, token: str) -> tuple[str, str]:
    status, body = _request(
        "POST",
        f"{cognito_config.api_base_url}/projects",
        token=token,
        body={"name": "cwd-integration-channel-authz"},
    )
    assert status == 201, body
    project_id = body["projectId"]

    status, body = _request(
        "POST",
        f"{cognito_config.api_base_url}/projects/{project_id}/conversations",
        token=token,
        body={},
    )
    assert status == 201, body
    return project_id, body["conversationId"]


def test_the_owner_can_subscribe_to_their_own_conversation_channel(
    cognito_config: CognitoConfig, events_config: EventsConfig, seeded_user_token: str
) -> None:
    project_id, conversation_id = _create_conversation(cognito_config, seeded_user_token)
    try:

        async def _run() -> dict[str, Any]:
            async with _connect(events_config, id_token=seeded_user_token) as ws:
                return await _subscribe(
                    ws,
                    channel=f"/conversations/{conversation_id}",
                    id_token=seeded_user_token,
                    http_domain=events_config.http_domain,
                )

        response = asyncio.run(_run())
        assert response["type"] == "subscribe_success", response
    finally:
        _request(
            "DELETE",
            f"{cognito_config.api_base_url}/projects/{project_id}",
            token=seeded_user_token,
        )


def test_a_non_owner_cannot_subscribe_to_the_conversation_channel(
    cognito_config: CognitoConfig,
    events_config: EventsConfig,
    seeded_user_token: str,
    second_user_token: str,
) -> None:
    project_id, conversation_id = _create_conversation(cognito_config, seeded_user_token)
    try:

        async def _run() -> dict[str, Any]:
            async with _connect(events_config, id_token=second_user_token) as ws:
                return await _subscribe(
                    ws,
                    channel=f"/conversations/{conversation_id}",
                    id_token=second_user_token,
                    http_domain=events_config.http_domain,
                )

        response = asyncio.run(_run())
        assert response["type"] != "subscribe_success", response
    finally:
        _request(
            "DELETE",
            f"{cognito_config.api_base_url}/projects/{project_id}",
            token=seeded_user_token,
        )


def test_no_user_token_can_publish_even_to_a_channel_they_own(
    cognito_config: CognitoConfig, events_config: EventsConfig, seeded_user_token: str
) -> None:
    """A Cognito-authenticated user publishing to their *own* conversation channel must still
    fail — ownership is irrelevant here, only the channel's auth *mode* (IAM only) is."""
    project_id, conversation_id = _create_conversation(cognito_config, seeded_user_token)
    try:

        async def _run() -> dict[str, Any]:
            async with _connect(events_config, id_token=seeded_user_token) as ws:
                return await _publish(
                    ws,
                    channel=f"/conversations/{conversation_id}",
                    id_token=seeded_user_token,
                    http_domain=events_config.http_domain,
                )

        response = asyncio.run(_run())
        assert response["type"] != "publish_success", response
    finally:
        _request(
            "DELETE",
            f"{cognito_config.api_base_url}/projects/{project_id}",
            token=seeded_user_token,
        )
