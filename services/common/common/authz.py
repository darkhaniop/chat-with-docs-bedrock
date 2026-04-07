"""Explicit, single-place authorization (docs/07-security.md#authorization).

Every route handler that touches a project- or document-scoped resource calls one of these
before any other work. Cross-tenant access raises `NotFound` — 404, never 403 — because there
is no legitimate reason for a caller to distinguish "does not exist" from "not yours"
(docs/05-api-contracts.md#conventions).
"""

from __future__ import annotations

from common.models import Conversation, Document, Project
from common.repo import NotFound, Repo

__all__ = ["NotFound", "require_conversation", "require_document", "require_project"]


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


def require_conversation(repo: Repo, owner_sub: str, conversation_id: str) -> Conversation:
    """Unlike `require_document`, there is no project id in the path to chain-verify against
    (docs/05-api-contracts.md's conversation/message routes are addressed by `conversationId`
    alone, never nested under `/projects/{projectId}/conversations/{conversationId}`) — `sub`
    denormalised onto the canonical item is the whole check."""
    conversation = repo.get_conversation(conversation_id)
    if conversation is None or conversation.owner_sub != owner_sub:
        raise NotFound(conversation_id)
    return conversation
