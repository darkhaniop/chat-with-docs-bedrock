"""docs/05-api-contracts.md#error-shape — the one error envelope every route returns."""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}

    def to_body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


def not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "The requested resource was not found.")


def bad_request(code: str, message: str, **details: Any) -> ApiError:
    return ApiError(400, code, message, details)


def too_large(code: str, message: str, **details: Any) -> ApiError:
    return ApiError(413, code, message, details)


def conflict(code: str, message: str, **details: Any) -> ApiError:
    return ApiError(409, code, message, details)
