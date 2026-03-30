from __future__ import annotations

from common.repo import NotFound, Repo


def test_create_project_writes_canonical_and_list_view(repo: Repo) -> None:
    project = repo.create_project(owner_sub="user-1", name="Ashford", description="due diligence")

    fetched = repo.get_project(project.project_id)
    assert fetched is not None
    assert fetched.name == "Ashford"
    assert fetched.owner_sub == "user-1"
    assert fetched.document_count == 0

    items, cursor = repo.list_projects("user-1", limit=20, cursor=None)
    assert cursor is None
    assert [i.project_id for i in items] == [project.project_id]
    assert items[0].name == "Ashford"


def test_get_project_returns_none_when_missing(repo: Repo) -> None:
    assert repo.get_project("nope") is None


def test_list_projects_only_returns_the_owners_projects(repo: Repo) -> None:
    repo.create_project(owner_sub="user-1", name="Mine", description="")
    repo.create_project(owner_sub="user-2", name="Theirs", description="")

    items, _ = repo.list_projects("user-1", limit=20, cursor=None)
    assert [i.name for i in items] == ["Mine"]


def test_list_projects_paginates_with_a_cursor(repo: Repo) -> None:
    for i in range(3):
        repo.create_project(owner_sub="user-1", name=f"p{i}", description="")

    page1, cursor1 = repo.list_projects("user-1", limit=2, cursor=None)
    assert len(page1) == 2
    assert cursor1 is not None

    page2, cursor2 = repo.list_projects("user-1", limit=2, cursor=cursor1)
    assert len(page2) == 1
    assert cursor2 is None
    assert {p.name for p in page1 + page2} == {"p0", "p1", "p2"}


def test_update_project_updates_canonical_and_list_view(repo: Repo) -> None:
    project = repo.create_project(owner_sub="user-1", name="old", description="old desc")

    updated = repo.update_project(project.project_id, "user-1", name="new", description=None)
    assert updated.name == "new"
    assert updated.description == "old desc"  # untouched field preserved

    items, _ = repo.list_projects("user-1", limit=20, cursor=None)
    assert items[0].name == "new"


def test_update_project_raises_not_found_for_missing_project(repo: Repo) -> None:
    try:
        repo.update_project("nope", "user-1", name="x", description=None)
        raise AssertionError("expected NotFound")
    except NotFound:
        pass


def test_delete_project_removes_both_items(repo: Repo) -> None:
    project = repo.create_project(owner_sub="user-1", name="gone", description="")

    repo.delete_project(project.project_id, "user-1")

    assert repo.get_project(project.project_id) is None
    items, _ = repo.list_projects("user-1", limit=20, cursor=None)
    assert items == []


def test_create_document_increments_project_document_count(repo: Repo) -> None:
    project = repo.create_project(owner_sub="user-1", name="p", description="")

    document = repo.create_document(
        project_id=project.project_id,
        owner_sub="user-1",
        filename="report.pdf",
        content_type="application/pdf",
        byte_size=1234,
        kind="pdf",
    )

    assert document.status == "PENDING"
    refreshed = repo.get_project(project.project_id)
    assert refreshed is not None
    assert refreshed.document_count == 1


def test_get_document_returns_full_shape_from_canonical_item(repo: Repo) -> None:
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    created = repo.create_document(
        project_id=project.project_id,
        owner_sub="user-1",
        filename="a.pdf",
        content_type="application/pdf",
        byte_size=10,
        kind="pdf",
    )

    fetched = repo.get_document(created.document_id)
    assert fetched is not None
    assert fetched.filename == "a.pdf"
    assert fetched.project_id == project.project_id


def test_list_documents_returns_full_document_shape(repo: Repo) -> None:
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    repo.create_document(
        project_id=project.project_id,
        owner_sub="user-1",
        filename="a.pdf",
        content_type="application/pdf",
        byte_size=10,
        kind="pdf",
    )

    items, cursor = repo.list_documents(project.project_id, limit=20, cursor=None)
    assert cursor is None
    assert len(items) == 1
    assert items[0].filename == "a.pdf"
    assert items[0].status == "PENDING"


def test_update_document_changes_status_and_preserves_other_fields(repo: Repo) -> None:
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    document = repo.create_document(
        project_id=project.project_id,
        owner_sub="user-1",
        filename="a.pdf",
        content_type="application/pdf",
        byte_size=10,
        kind="pdf",
    )

    updated = repo.update_document(document.document_id, status="UPLOADED")
    assert updated.status == "UPLOADED"
    assert updated.filename == "a.pdf"

    from_list, _ = repo.list_documents(project.project_id, limit=20, cursor=None)
    assert from_list[0].status == "UPLOADED"


def test_delete_document_removes_both_items_and_decrements_count(repo: Repo) -> None:
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    document = repo.create_document(
        project_id=project.project_id,
        owner_sub="user-1",
        filename="a.pdf",
        content_type="application/pdf",
        byte_size=10,
        kind="pdf",
    )

    repo.delete_document(document)

    assert repo.get_document(document.document_id) is None
    items, _ = repo.list_documents(project.project_id, limit=20, cursor=None)
    assert items == []
    refreshed = repo.get_project(project.project_id)
    assert refreshed is not None
    assert refreshed.document_count == 0


def test_list_all_documents_paginates_internally(repo: Repo) -> None:
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    for i in range(3):
        repo.create_document(
            project_id=project.project_id,
            owner_sub="user-1",
            filename=f"{i}.pdf",
            content_type="application/pdf",
            byte_size=10,
            kind="pdf",
        )

    documents = repo.list_all_documents(project.project_id)
    assert {d.filename for d in documents} == {"0.pdf", "1.pdf", "2.pdf"}


def test_scan_stale_pending_documents_excludes_ready_and_recent(repo: Repo) -> None:
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    stale = repo.create_document(
        project_id=project.project_id,
        owner_sub="user-1",
        filename="stale.pdf",
        content_type="application/pdf",
        byte_size=10,
        kind="pdf",
    )
    ready = repo.create_document(
        project_id=project.project_id,
        owner_sub="user-1",
        filename="ready.pdf",
        content_type="application/pdf",
        byte_size=10,
        kind="pdf",
    )
    repo.update_document(ready.document_id, status="READY")

    stale_results = repo.scan_stale_pending_documents(older_than_iso="9999-01-01T00:00:00Z")
    assert [d.document_id for d in stale_results] == [stale.document_id]

    none_are_old_enough = repo.scan_stale_pending_documents(older_than_iso="2000-01-01T00:00:00Z")
    assert none_are_old_enough == []
