"""HTTP API Lambda entry point. Routes are dispatched on `routeKey` (HTTP API v2 payload
format). Handlers are thin: parse -> authorize -> call a service function -> serialise.
"""

from __future__ import annotations

import json
import os
from typing import Any

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger(service="api")

_JSON_HEADERS = {"Content-Type": "application/json"}


def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    route_key = event.get("routeKey", "")
    request_context = event.get("requestContext", {})
    logger.append_keys(requestId=request_context.get("requestId"), routeKey=route_key)

    if route_key == "GET /health":
        return _health()
    if route_key == "GET /echo":
        return _echo(event)

    logger.warning("no handler for route")
    return _response(404, {"error": {"code": "NOT_FOUND", "message": "No such route."}})


def _health() -> dict[str, Any]:
    return _response(
        200,
        {
            "status": "ok",
            "version": os.environ.get("CWD_VERSION", "unknown"),
            "commit": os.environ.get("CWD_COMMIT", "unknown"),
        },
    )


def _echo(event: dict[str, Any]) -> dict[str, Any]:
    # The Lambda re-reads `sub` from the authorizer's validated claims and never trusts a
    # body-supplied identity.
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    sub = claims.get("sub")
    logger.append_keys(userSub=sub)
    return _response(200, {"message": "echo", "sub": sub})


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status_code, "headers": _JSON_HEADERS, "body": json.dumps(body)}
