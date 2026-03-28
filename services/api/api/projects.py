"""Project route handlers' business logic (docs/05-api-contracts.md#projects). Thin: parse is
done by the caller (`handler.py`), this module authorizes, calls `common.repo`, and shapes the
response.
"""

from __future__ import annotations

from typing import Any

from api import documents, validation
from common import authz
from common.repo import Repo
from common.storage import DocumentsStore


def create(repo: Repo, owner_sub: str, body: dict[str, Any]) -> dict[str, Any]:
    name, description = validation.validate_create_project(body)
    project = repo.create_project(owner_sub=owner_sub, name=name, description=description)
    return project.to_api()


def list_for_owner(repo: Repo, owner_sub: str, query: dict[str, str] | None) -> dict[str, Any]:
    limit, cursor = validation.validate_pagination(query)
    items, next_cursor = repo.list_projects(owner_sub, limit=limit, cursor=cursor)
    return {"items": [i.to_api() for i in items], "nextCursor": next_cursor}


def get(repo: Repo, owner_sub: str, project_id: str) -> dict[str, Any]:
    project = authz.require_project(repo, owner_sub, project_id)
    return project.to_api()


def patch(repo: Repo, owner_sub: str, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
    authz.require_project(repo, owner_sub, project_id)
    name, description = validation.validate_patch_project(body)
    project = repo.update_project(project_id, owner_sub, name=name, description=description)
    return project.to_api()


def delete(repo: Repo, store: DocumentsStore, owner_sub: str, project_id: str) -> dict[str, Any]:
    """docs/05-api-contracts.md#projects: flips status and returns immediately. There is no
    ingestion pipeline yet (Phase 3), so every document under the project is still just
    metadata plus one `raw/` object — cheap enough to clean up inline rather than via a
    separate queued cleanup job. Revisit once documents can carry pages/chunks/vectors."""
    authz.require_project(repo, owner_sub, project_id)
    for document in repo.list_all_documents(project_id):
        documents.delete_storage(store, document)
        repo.delete_document(document)
    repo.delete_project(project_id, owner_sub)
    return {"status": "DELETING"}
