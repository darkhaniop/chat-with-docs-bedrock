"""Document route handlers' business logic (docs/05-api-contracts.md#documents)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from api import validation
from api.errors import bad_request
from common import authz
from common.config import Settings, get_settings
from common.models import Document
from common.repo import Repo
from common.storage import DocumentsStore, render_key, source_key


def _expiry(settings: Settings) -> str:
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.presigned_url_ttl_seconds)
    return expires_at.strftime("%Y-%m-%dT%H:%M:%SZ")


def delete_storage(store: DocumentsStore, document: Document) -> None:
    store.delete_prefix(f"raw/{document.project_id}/{document.document_id}/")
    store.delete_prefix(f"pages/{document.project_id}/{document.document_id}/")


def create(
    repo: Repo, store: DocumentsStore, owner_sub: str, project_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    authz.require_project(repo, owner_sub, project_id)
    settings = get_settings()
    filename, content_type, byte_size, kind = validation.validate_create_document(body, settings)
    document = repo.create_document(
        project_id=project_id,
        owner_sub=owner_sub,
        filename=filename,
        content_type=content_type,
        byte_size=byte_size,
        kind=kind,
    )
    extension = validation.extension_for_content_type(content_type)
    key = source_key(project_id, document.document_id, extension)
    url = store.presign_put(key, content_type=content_type)
    return {
        "document": document.to_api(),
        "upload": {
            "url": url,
            "method": "PUT",
            "headers": {"Content-Type": content_type},
            "expiresAt": _expiry(settings),
        },
    }


def list_for_project(
    repo: Repo, owner_sub: str, project_id: str, query: dict[str, str] | None
) -> dict[str, Any]:
    authz.require_project(repo, owner_sub, project_id)
    limit, cursor = validation.validate_pagination(query)
    items, next_cursor = repo.list_documents(project_id, limit=limit, cursor=cursor)
    return {"items": [d.to_api() for d in items], "nextCursor": next_cursor}


def get(repo: Repo, owner_sub: str, project_id: str, document_id: str) -> dict[str, Any]:
    document = authz.require_document(repo, owner_sub, project_id, document_id)
    return document.to_api()


def delete(
    repo: Repo, store: DocumentsStore, owner_sub: str, project_id: str, document_id: str
) -> dict[str, Any]:
    document = authz.require_document(repo, owner_sub, project_id, document_id)
    delete_storage(store, document)
    repo.delete_document(document)
    return {"status": "DELETING"}


def source_url(
    repo: Repo, store: DocumentsStore, owner_sub: str, project_id: str, document_id: str
) -> dict[str, Any]:
    document = authz.require_document(repo, owner_sub, project_id, document_id)
    settings = get_settings()
    extension = validation.extension_for_content_type(document.content_type)
    key = source_key(project_id, document_id, extension)
    return {"url": store.presign_get(key), "expiresAt": _expiry(settings)}


def render_url(
    repo: Repo,
    store: DocumentsStore,
    owner_sub: str,
    project_id: str,
    document_id: str,
    page_raw: str,
) -> dict[str, Any]:
    document = authz.require_document(repo, owner_sub, project_id, document_id)
    page_number = validation.validate_page_number(page_raw)
    if document.page_count is not None and page_number > document.page_count:
        raise bad_request(
            "VALIDATION_ERROR",
            f"Page {page_number} does not exist; the document has {document.page_count} pages.",
        )
    settings = get_settings()
    key = render_key(project_id, document_id, page_number)
    return {"url": store.presign_get(key), "expiresAt": _expiry(settings)}
