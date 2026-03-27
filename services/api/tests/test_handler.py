from __future__ import annotations

import json
from typing import Any

from api.handler import lambda_handler


def _event(route_key: str, *, claims: dict[str, Any] | None = None) -> dict[str, Any]:
    request_context: dict[str, Any] = {"requestId": "req-1"}
    if claims is not None:
        request_context["authorizer"] = {"jwt": {"claims": claims}}
    return {"routeKey": route_key, "requestContext": request_context}


def test_health_is_unauthenticated_and_reports_status_ok() -> None:
    response = lambda_handler(_event("GET /health"), context=None)  # type: ignore[arg-type]

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["status"] == "ok"
    assert "version" in body
    assert "commit" in body


def test_echo_returns_the_authorizers_validated_sub() -> None:
    event = _event("GET /echo", claims={"sub": "user-123"})

    response = lambda_handler(event, context=None)  # type: ignore[arg-type]

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["sub"] == "user-123"


def test_unknown_route_returns_404_error_envelope() -> None:
    response = lambda_handler(_event("GET /nope"), context=None)  # type: ignore[arg-type]

    assert response["statusCode"] == 404
    body = json.loads(response["body"])
    assert body["error"]["code"] == "NOT_FOUND"
