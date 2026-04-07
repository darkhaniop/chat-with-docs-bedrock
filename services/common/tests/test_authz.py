from __future__ import annotations

import pytest

from common import authz
from common.repo import NotFound, Repo


def test_require_project_returns_the_project_for_its_owner(repo: Repo) -> None:
    project = repo.create_project(owner_sub="user-1", name="p", description="")

    found = authz.require_project(repo, "user-1", project.project_id)

    assert found.project_id == project.project_id


def test_require_project_404s_for_a_different_owner(repo: Repo) -> None:
    project = repo.create_project(owner_sub="user-1", name="p", description="")

    with pytest.raises(NotFound):
        authz.require_project(repo, "user-2", project.project_id)


def test_require_project_404s_for_a_missing_project(repo: Repo) -> None:
    with pytest.raises(NotFound):
        authz.require_project(repo, "user-1", "does-not-exist")


def test_require_document_returns_the_document_for_the_right_chain(repo: Repo) -> None:
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    document = repo.create_document(
        project_id=project.project_id,
        owner_sub="user-1",
        filename="a.pdf",
        content_type="application/pdf",
        byte_size=10,
        kind="pdf",
    )

    found = authz.require_document(repo, "user-1", project.project_id, document.document_id)

    assert found.document_id == document.document_id


def test_require_document_404s_when_project_id_in_path_does_not_match_document(
    repo: Repo,
) -> None:
    project_a = repo.create_project(owner_sub="user-1", name="a", description="")
    project_b = repo.create_project(owner_sub="user-1", name="b", description="")
    document = repo.create_document(
        project_id=project_a.project_id,
        owner_sub="user-1",
        filename="a.pdf",
        content_type="application/pdf",
        byte_size=10,
        kind="pdf",
    )

    # Same owner, but the path's projectId doesn't match the document's actual project — ids
    # from the path must never be trusted to be consistent with each other.
    with pytest.raises(NotFound):
        authz.require_document(repo, "user-1", project_b.project_id, document.document_id)


def test_require_document_404s_for_a_different_owner(repo: Repo) -> None:
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    document = repo.create_document(
        project_id=project.project_id,
        owner_sub="user-1",
        filename="a.pdf",
        content_type="application/pdf",
        byte_size=10,
        kind="pdf",
    )

    with pytest.raises(NotFound):
        authz.require_document(repo, "user-2", project.project_id, document.document_id)


def test_require_conversation_returns_the_conversation_for_its_owner(repo: Repo) -> None:
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    conversation = repo.create_conversation(
        project_id=project.project_id, owner_sub="user-1", title="", pinned_document_ids=[]
    )

    found = authz.require_conversation(repo, "user-1", conversation.conversation_id)

    assert found.conversation_id == conversation.conversation_id


def test_require_conversation_404s_for_a_different_owner(repo: Repo) -> None:
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    conversation = repo.create_conversation(
        project_id=project.project_id, owner_sub="user-1", title="", pinned_document_ids=[]
    )

    with pytest.raises(NotFound):
        authz.require_conversation(repo, "user-2", conversation.conversation_id)


def test_require_conversation_404s_for_a_missing_conversation(repo: Repo) -> None:
    with pytest.raises(NotFound):
        authz.require_conversation(repo, "user-1", "does-not-exist")
