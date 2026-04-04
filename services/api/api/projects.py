"""Project route handlers' business logic (docs/05-api-contracts.md#projects). Thin: parse is
done by the caller (`handler.py`), this module authorizes, calls `common.repo`, and shapes the
response.
"""

from __future__ import annotations

from typing import Any

from api import documents, validation
from common import authz
from common.config import get_settings
from common.repo import Repo
from common.storage import DocumentsStore
from common.vectors import VectorIndexProtocol


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


def delete(
    repo: Repo,
    store: DocumentsStore,
    vectors: VectorIndexProtocol,
    owner_sub: str,
    project_id: str,
) -> dict[str, Any]:
    """docs/05-api-contracts.md#projects: flips status and returns immediately, cleaning up
    inline rather than via a separate queued job. docs/02-data-model.md#s3-vectors: "Deleting a
    project deletes the whole index" — one `delete_index_if_present` call rather than per-
    document vector deletion, since every document in the project shares the same index.
    Per-document Page/Chunk cleanup (`delete_pages_and_chunks`) is added here in Phase 4 — a gap
    left over from Phase 2, when a project's documents were still just metadata plus a `raw/`
    object with nothing else to clean up."""
    authz.require_project(repo, owner_sub, project_id)
    for document in repo.list_all_documents(project_id):
        documents.delete_storage(store, document)
        repo.delete_pages_and_chunks(document.document_id)
        repo.delete_document(document)
    vectors.delete_index_if_present(get_settings().vector_index_name(project_id))
    repo.delete_project(project_id, owner_sub)
    return {"status": "DELETING"}
