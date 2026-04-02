"""Request body/query validation for the project and document routes
(docs/05-api-contracts.md). Every failure raises `ApiError`; handlers never construct the error
envelope by hand.
"""

from __future__ import annotations

import json
from typing import Any

from api.errors import bad_request, too_large
from common.config import Settings
from common.content_types import ACCEPTED_CONTENT_TYPES, extension_for_content_type
from common.models import DocumentKind

__all__ = [
    "extension_for_content_type",
    "validate_create_document",
    "validate_create_project",
    "validate_page_number",
    "validate_pagination",
    "validate_patch_project",
]

_MAX_NAME_LENGTH = 200
_MAX_DESCRIPTION_LENGTH = 2000
_MAX_FILENAME_LENGTH = 255
_DEFAULT_PAGE_LIMIT = 20
_MAX_PAGE_LIMIT = 100


def parse_body(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise bad_request("MALFORMED_BODY", "Request body is not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise bad_request("MALFORMED_BODY", "Request body must be a JSON object.")
    return parsed


def _require_str(body: dict[str, Any], field: str, *, max_length: int) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value.strip():
        raise bad_request("VALIDATION_ERROR", f"`{field}` is required.", field=field)
    if len(value) > max_length:
        raise bad_request(
            "VALIDATION_ERROR", f"`{field}` exceeds {max_length} characters.", field=field
        )
    return value


def _optional_str(body: dict[str, Any], field: str, *, max_length: int) -> str | None:
    if field not in body or body[field] is None:
        return None
    value = body[field]
    if not isinstance(value, str):
        raise bad_request("VALIDATION_ERROR", f"`{field}` must be a string.", field=field)
    if len(value) > max_length:
        raise bad_request(
            "VALIDATION_ERROR", f"`{field}` exceeds {max_length} characters.", field=field
        )
    return value


def validate_create_project(body: dict[str, Any]) -> tuple[str, str]:
    name = _require_str(body, "name", max_length=_MAX_NAME_LENGTH)
    description = _optional_str(body, "description", max_length=_MAX_DESCRIPTION_LENGTH) or ""
    return name, description


def validate_patch_project(body: dict[str, Any]) -> tuple[str | None, str | None]:
    name = _optional_str(body, "name", max_length=_MAX_NAME_LENGTH)
    description = _optional_str(body, "description", max_length=_MAX_DESCRIPTION_LENGTH)
    return name, description


def validate_create_document(
    body: dict[str, Any], settings: Settings
) -> tuple[str, str, int, DocumentKind]:
    filename = _require_str(body, "filename", max_length=_MAX_FILENAME_LENGTH)
    content_type = _require_str(body, "contentType", max_length=100)
    if content_type not in ACCEPTED_CONTENT_TYPES:
        raise bad_request(
            "UNSUPPORTED_CONTENT_TYPE",
            f"`{content_type}` is not a supported document type.",
            contentType=content_type,
            accepted=sorted(ACCEPTED_CONTENT_TYPES),
        )
    byte_size = body.get("byteSize")
    if not isinstance(byte_size, int) or isinstance(byte_size, bool) or byte_size <= 0:
        raise bad_request("VALIDATION_ERROR", "`byteSize` must be a positive integer.")
    if byte_size > settings.max_document_bytes:
        raise too_large(
            "DOCUMENT_TOO_LARGE",
            f"The document is {byte_size} bytes; the limit is {settings.max_document_bytes}.",
            byteSize=byte_size,
            limit=settings.max_document_bytes,
        )
    kind = ACCEPTED_CONTENT_TYPES[content_type][0]
    return filename, content_type, byte_size, kind


def validate_pagination(query: dict[str, str] | None) -> tuple[int, str | None]:
    query = query or {}
    limit = _DEFAULT_PAGE_LIMIT
    if "limit" in query:
        try:
            limit = int(query["limit"])
        except ValueError as exc:
            raise bad_request("VALIDATION_ERROR", "`limit` must be an integer.") from exc
        if not 1 <= limit <= _MAX_PAGE_LIMIT:
            raise bad_request(
                "VALIDATION_ERROR", f"`limit` must be between 1 and {_MAX_PAGE_LIMIT}."
            )
    cursor = query.get("cursor")
    return limit, cursor


def validate_page_number(raw: str) -> int:
    try:
        page = int(raw)
    except ValueError as exc:
        raise bad_request("VALIDATION_ERROR", "Page number must be an integer.") from exc
    if page < 1:
        raise bad_request("VALIDATION_ERROR", "Page number must be at least 1.")
    return page
