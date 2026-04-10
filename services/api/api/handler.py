"""HTTP API Lambda entry point. Routes are dispatched on `routeKey` (HTTP API v2 payload
format). Handlers are thin: parse -> authorize -> call a service function -> serialise.
"""

from __future__ import annotations

import json
import os
from typing import Any

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

from api import conversations, deps, documents, projects
from api.errors import ApiError, bad_request
from api.validation import parse_body
from common.repo import NotFound

logger = Logger(service="api")

_JSON_HEADERS = {"Content-Type": "application/json"}


def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    route_key = event.get("routeKey", "")
    request_context = event.get("requestContext", {})
    logger.append_keys(requestId=request_context.get("requestId"), routeKey=route_key)

    try:
        return _dispatch(route_key, event)
    except ApiError as error:
        logger.warning("request failed", code=error.code, statusCode=error.status_code)
        return _response(error.status_code, error.to_body())
    except NotFound:
        logger.warning("resource not found or not owned by caller")
        not_found = ApiError(404, "NOT_FOUND", "The requested resource was not found.")
        return _response(not_found.status_code, not_found.to_body())


def _dispatch(route_key: str, event: dict[str, Any]) -> dict[str, Any]:
    if route_key == "GET /health":
        return _health()

    if route_key == "POST /projects":
        owner_sub = _owner_sub(event)
        body = _body(event)
        return _response(201, projects.create(deps.get_repo(), owner_sub, body))

    if route_key == "GET /projects":
        owner_sub = _owner_sub(event)
        return _response(200, projects.list_for_owner(deps.get_repo(), owner_sub, _query(event)))

    if route_key == "GET /projects/{projectId}":
        owner_sub = _owner_sub(event)
        project_id = _path(event, "projectId")
        return _response(200, projects.get(deps.get_repo(), owner_sub, project_id))

    if route_key == "PATCH /projects/{projectId}":
        owner_sub = _owner_sub(event)
        project_id = _path(event, "projectId")
        return _response(200, projects.patch(deps.get_repo(), owner_sub, project_id, _body(event)))

    if route_key == "DELETE /projects/{projectId}":
        owner_sub = _owner_sub(event)
        project_id = _path(event, "projectId")
        return _response(
            202,
            projects.delete(
                deps.get_repo(), deps.get_store(), deps.get_vector_index(), owner_sub, project_id
            ),
        )

    if route_key == "POST /projects/{projectId}/documents":
        owner_sub = _owner_sub(event)
        project_id = _path(event, "projectId")
        return _response(
            201,
            documents.create(
                deps.get_repo(), deps.get_store(), owner_sub, project_id, _body(event)
            ),
        )

    if route_key == "GET /projects/{projectId}/documents":
        owner_sub = _owner_sub(event)
        project_id = _path(event, "projectId")
        return _response(
            200,
            documents.list_for_project(deps.get_repo(), owner_sub, project_id, _query(event)),
        )

    if route_key == "GET /projects/{projectId}/documents/{documentId}":
        owner_sub = _owner_sub(event)
        project_id = _path(event, "projectId")
        document_id = _path(event, "documentId")
        return _response(200, documents.get(deps.get_repo(), owner_sub, project_id, document_id))

    if route_key == "DELETE /projects/{projectId}/documents/{documentId}":
        owner_sub = _owner_sub(event)
        project_id = _path(event, "projectId")
        document_id = _path(event, "documentId")
        return _response(
            202,
            documents.delete(
                deps.get_repo(),
                deps.get_store(),
                deps.get_vector_index(),
                owner_sub,
                project_id,
                document_id,
            ),
        )

    if route_key == "GET /projects/{projectId}/documents/{documentId}/source-url":
        owner_sub = _owner_sub(event)
        project_id = _path(event, "projectId")
        document_id = _path(event, "documentId")
        return _response(
            200,
            documents.source_url(
                deps.get_repo(), deps.get_store(), owner_sub, project_id, document_id
            ),
        )

    if route_key == "POST /projects/{projectId}/documents/{documentId}/ingest":
        owner_sub = _owner_sub(event)
        project_id = _path(event, "projectId")
        document_id = _path(event, "documentId")
        return _response(
            202,
            documents.ingest(
                deps.get_repo(),
                deps.get_store(),
                deps.get_workflow(),
                deps.get_vector_index(),
                owner_sub,
                project_id,
                document_id,
            ),
        )

    if route_key == "GET /projects/{projectId}/documents/{documentId}/pages/{page}/render-url":
        owner_sub = _owner_sub(event)
        project_id = _path(event, "projectId")
        document_id = _path(event, "documentId")
        page = _path(event, "page")
        return _response(
            200,
            documents.render_url(
                deps.get_repo(), deps.get_store(), owner_sub, project_id, document_id, page
            ),
        )

    if route_key == "POST /projects/{projectId}/conversations":
        owner_sub = _owner_sub(event)
        project_id = _path(event, "projectId")
        return _response(
            201, conversations.create(deps.get_repo(), owner_sub, project_id, _body(event))
        )

    if route_key == "GET /projects/{projectId}/conversations":
        owner_sub = _owner_sub(event)
        project_id = _path(event, "projectId")
        return _response(
            200,
            conversations.list_for_project(deps.get_repo(), owner_sub, project_id, _query(event)),
        )

    if route_key == "GET /conversations/{conversationId}":
        owner_sub = _owner_sub(event)
        conversation_id = _path(event, "conversationId")
        return _response(200, conversations.get(deps.get_repo(), owner_sub, conversation_id))

    if route_key == "PATCH /conversations/{conversationId}":
        owner_sub = _owner_sub(event)
        conversation_id = _path(event, "conversationId")
        return _response(
            200,
            conversations.patch(deps.get_repo(), owner_sub, conversation_id, _body(event)),
        )

    if route_key == "DELETE /conversations/{conversationId}":
        owner_sub = _owner_sub(event)
        conversation_id = _path(event, "conversationId")
        conversations.delete(deps.get_repo(), owner_sub, conversation_id)
        return _response(204)

    if route_key == "GET /conversations/{conversationId}/messages":
        owner_sub = _owner_sub(event)
        conversation_id = _path(event, "conversationId")
        return _response(
            200,
            conversations.list_messages(deps.get_repo(), owner_sub, conversation_id, _query(event)),
        )

    if route_key == "POST /conversations/{conversationId}/messages":
        owner_sub = _owner_sub(event)
        conversation_id = _path(event, "conversationId")
        return _response(
            202,
            conversations.post_message(
                deps.get_repo(),
                deps.get_answer_queue(),
                owner_sub,
                conversation_id,
                _body(event),
            ),
        )

    if route_key == "POST /conversations/{conversationId}/messages/{messageId}/cancel":
        owner_sub = _owner_sub(event)
        conversation_id = _path(event, "conversationId")
        message_id = _path(event, "messageId")
        return _response(
            202,
            conversations.cancel_message(deps.get_repo(), owner_sub, conversation_id, message_id),
        )

    logger.warning("no handler for route")
    not_found = ApiError(404, "NOT_FOUND", "No such route.")
    return _response(not_found.status_code, not_found.to_body())


def _owner_sub(event: dict[str, Any]) -> str:
    # The Lambda re-reads `sub` from the authorizer's already-validated claims and never trusts a
    # body-supplied identity.
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    sub = claims.get("sub")
    if not sub:
        raise bad_request("VALIDATION_ERROR", "Missing authenticated caller.")
    logger.append_keys(userSub=sub)
    return str(sub)


def _path(event: dict[str, Any], name: str) -> str:
    value = event.get("pathParameters", {}).get(name)
    if not value:
        raise bad_request("VALIDATION_ERROR", f"Missing path parameter `{name}`.")
    return str(value)


def _query(event: dict[str, Any]) -> dict[str, str]:
    params = event.get("queryStringParameters")
    return dict(params) if params else {}


def _body(event: dict[str, Any]) -> dict[str, Any]:
    return parse_body(event.get("body"))


def _health() -> dict[str, Any]:
    return _response(
        200,
        {
            "status": "ok",
            "version": os.environ.get("CWD_VERSION", "unknown"),
            "commit": os.environ.get("CWD_COMMIT", "unknown"),
        },
    )


def _response(status_code: int, body: dict[str, Any] | None = None) -> dict[str, Any]:
    response: dict[str, Any] = {"statusCode": status_code, "headers": _JSON_HEADERS}
    if body is not None:
        response["body"] = json.dumps(body)
    return response
