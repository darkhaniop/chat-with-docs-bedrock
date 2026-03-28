from __future__ import annotations

import pytest

from api import validation
from api.errors import ApiError
from common.config import get_settings


def test_validate_create_project_requires_a_non_empty_name() -> None:
    with pytest.raises(ApiError) as exc_info:
        validation.validate_create_project({"name": "  "})
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "VALIDATION_ERROR"


def test_validate_create_project_defaults_description_to_empty_string() -> None:
    name, description = validation.validate_create_project({"name": "Ashford"})
    assert name == "Ashford"
    assert description == ""


def test_validate_patch_project_allows_partial_updates() -> None:
    name, description = validation.validate_patch_project({"name": "new"})
    assert name == "new"
    assert description is None


def test_validate_create_document_rejects_unsupported_content_type() -> None:
    body = {"filename": "a.docx", "contentType": "application/msword", "byteSize": 10}
    with pytest.raises(ApiError) as exc_info:
        validation.validate_create_document(body, get_settings())
    assert exc_info.value.code == "UNSUPPORTED_CONTENT_TYPE"


def test_validate_create_document_rejects_non_positive_byte_size() -> None:
    body = {"filename": "a.pdf", "contentType": "application/pdf", "byteSize": 0}
    with pytest.raises(ApiError) as exc_info:
        validation.validate_create_document(body, get_settings())
    assert exc_info.value.status_code == 400


def test_validate_create_document_rejects_oversized_documents() -> None:
    settings = get_settings()
    body = {
        "filename": "a.pdf",
        "contentType": "application/pdf",
        "byteSize": settings.max_document_bytes + 1,
    }
    with pytest.raises(ApiError) as exc_info:
        validation.validate_create_document(body, settings)
    assert exc_info.value.status_code == 413
    assert exc_info.value.code == "DOCUMENT_TOO_LARGE"


def test_validate_create_document_accepts_every_documented_content_type() -> None:
    settings = get_settings()
    for content_type in ("application/pdf", "image/png", "image/jpeg", "image/webp"):
        body = {"filename": "a", "contentType": content_type, "byteSize": 10}
        filename, ct, byte_size, kind = validation.validate_create_document(body, settings)
        assert ct == content_type
        assert kind in ("pdf", "image")


def test_validate_pagination_defaults_and_bounds() -> None:
    limit, cursor = validation.validate_pagination(None)
    assert limit == 20
    assert cursor is None

    with pytest.raises(ApiError):
        validation.validate_pagination({"limit": "0"})
    with pytest.raises(ApiError):
        validation.validate_pagination({"limit": "101"})
    with pytest.raises(ApiError):
        validation.validate_pagination({"limit": "not-a-number"})


def test_validate_page_number_rejects_zero_and_negative() -> None:
    assert validation.validate_page_number("1") == 1
    with pytest.raises(ApiError):
        validation.validate_page_number("0")
    with pytest.raises(ApiError):
        validation.validate_page_number("-1")


def test_parse_body_rejects_malformed_json() -> None:
    with pytest.raises(ApiError) as exc_info:
        validation.parse_body("{not json")
    assert exc_info.value.code == "MALFORMED_BODY"


def test_parse_body_rejects_a_json_array() -> None:
    with pytest.raises(ApiError):
        validation.parse_body("[1, 2, 3]")


def test_parse_body_treats_empty_body_as_empty_object() -> None:
    assert validation.parse_body(None) == {}
    assert validation.parse_body("") == {}
