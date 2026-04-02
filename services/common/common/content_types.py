"""Accepted document content types (docs/05-api-contracts.md#documents), shared by `api`
(upload validation) and `services/ingestion` (deriving the `raw/` source object's extension from
the Document item's stored `contentType`, since `ingest-probe` never re-parses the raw bytes to
guess a file extension).
"""

from __future__ import annotations

from common.models import DocumentKind

ACCEPTED_CONTENT_TYPES: dict[str, tuple[DocumentKind, str]] = {
    "application/pdf": ("pdf", "pdf"),
    "image/png": ("image", "png"),
    "image/jpeg": ("image", "jpg"),
    "image/webp": ("image", "webp"),
}


def extension_for_content_type(content_type: str) -> str:
    return ACCEPTED_CONTENT_TYPES[content_type][1]


def kind_for_content_type(content_type: str) -> DocumentKind:
    return ACCEPTED_CONTENT_TYPES[content_type][0]
