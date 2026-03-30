"""Explicit, single-place authorization (docs/07-security.md#authorization).

Every route handler that touches a project- or document-scoped resource calls one of these
before any other work. Cross-tenant access raises `NotFound` — 404, never 403 — because there
is no legitimate reason for a caller to distinguish "does not exist" from "not yours"
(docs/05-api-contracts.md#conventions).

`require_conversation` is not implemented here yet: Conversation items don't exist until Phase
5 writes them, and an authz check with nothing behind it is untested dead code. Add it
alongside `services/answering`'s first conversation-scoped route.
"""

from __future__ import annotations

from common.models import Document, Project
from common.repo import NotFound, Repo

__all__ = ["NotFound", "require_document", "require_project"]


def require_project(repo: Repo, owner_sub: str, project_id: str) -> Project:
    project = repo.get_project(project_id)
    if project is None or project.owner_sub != owner_sub:
        raise NotFound(project_id)
    return project


def require_document(repo: Repo, owner_sub: str, project_id: str, document_id: str) -> Document:
    """Verifies the *whole* chain — the document exists, belongs to the given project, and that
    project belongs to the caller. Ids from the path are never trusted to be consistent with
    each other."""
    require_project(repo, owner_sub, project_id)
    document = repo.get_document(document_id)
    if document is None or document.project_id != project_id or document.owner_sub != owner_sub:
        raise NotFound(document_id)
    return document
