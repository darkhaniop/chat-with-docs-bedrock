from __future__ import annotations

from common.models import Chunk, Page, PageRenders, Sentence
from common.repo import NotFound, Repo, new_id, next_id_after, now_iso


def test_next_id_after_always_sorts_strictly_after_the_given_id() -> None:
    # Two `new_id()` calls made back-to-back are *not* reliably ordered — confirmed empirically
    # (`next_id_after`'s own docstring) — which is exactly the bug this function exists to avoid
    # (api/conversations.py's `post_message`: the assistant reply sorting before the question it
    # answered). Run many trials since the failure mode is probabilistic by nature.
    for _ in range(1000):
        previous = new_id()
        assert next_id_after(previous) > previous


def test_next_id_after_is_deterministic_for_the_same_input() -> None:
    previous = new_id()
    assert next_id_after(previous) == next_id_after(previous)


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


def test_update_document_ingestion_merges_rather_than_replaces(repo: Repo) -> None:
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    document = repo.create_document(
        project_id=project.project_id,
        owner_sub="user-1",
        filename="a.pdf",
        content_type="application/pdf",
        byte_size=10,
        kind="pdf",
    )
    repo.update_document_ingestion(document.document_id, execution_arn="arn:aws:states:...")

    updated = repo.update_document_ingestion(document.document_id, started_at=now_iso())

    assert updated.ingestion.execution_arn == "arn:aws:states:..."
    assert updated.ingestion.started_at is not None


def test_update_document_ingestion_raises_not_found_for_missing_document(repo: Repo) -> None:
    try:
        repo.update_document_ingestion("nope", started_at=now_iso())
        raise AssertionError("expected NotFound")
    except NotFound:
        pass


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


def _make_document(repo: Repo) -> tuple[str, str]:
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    document = repo.create_document(
        project_id=project.project_id,
        owner_sub="user-1",
        filename="a.pdf",
        content_type="application/pdf",
        byte_size=10,
        kind="pdf",
    )
    return project.project_id, document.document_id


def test_increment_chunk_count_updates_canonical_only(repo: Repo) -> None:
    project_id, _ = _make_document(repo)

    repo.increment_chunk_count(project_id, by=42)

    refreshed = repo.get_project(project_id)
    assert refreshed is not None
    assert refreshed.chunk_count == 42


def test_put_page_then_get_page_round_trips(repo: Repo) -> None:
    _, document_id = _make_document(repo)
    page = Page(
        document_id=document_id,
        page_number=1,
        width=612.0,
        height=792.0,
        rotation=0,
        text_source="pdf",
        text_density=0.8,
    )

    repo.put_page(page)

    fetched = repo.get_page(document_id, 1)
    assert fetched is not None
    assert fetched.width == 612.0
    assert fetched.s3 is None


def test_put_page_replaces_wholesale_to_add_render_keys(repo: Repo) -> None:
    _, document_id = _make_document(repo)
    page = Page(
        document_id=document_id,
        page_number=1,
        width=612.0,
        height=792.0,
        text_source="pdf",
        text_density=0.8,
    )
    repo.put_page(page)

    updated = page.model_copy(
        update={"s3": PageRenders(display="pages/p/d/0001.png", embed="pages/p/d/0001.embed.jpg")}
    )
    repo.put_page(updated)

    fetched = repo.get_page(document_id, 1)
    assert fetched is not None
    assert fetched.s3 is not None
    assert fetched.s3.display == "pages/p/d/0001.png"


def test_list_pages_returns_all_pages_in_order(repo: Repo) -> None:
    _, document_id = _make_document(repo)
    for n in (2, 1, 3):
        repo.put_page(
            Page(
                document_id=document_id,
                page_number=n,
                width=612.0,
                height=792.0,
                text_source="pdf",
                text_density=0.8,
            )
        )

    pages = repo.list_pages(document_id)
    assert [p.page_number for p in pages] == [1, 2, 3]


def test_get_page_returns_none_when_missing(repo: Repo) -> None:
    _, document_id = _make_document(repo)
    assert repo.get_page(document_id, 1) is None


def _make_chunk(document_id: str, project_id: str, chunk_id: str, *, page_number: int = 1) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        document_id=document_id,
        project_id=project_id,
        page_number=page_number,
        ordinal=0,
        text="The facility achieved 94% uptime in Q3.",
        sentences=[
            Sentence(
                i=0,
                text="The facility achieved 94% uptime in Q3.",
                rects=[(72.0, 640.2, 511.4, 655.8)],
            )
        ],
        token_estimate=10,
        created_at=now_iso(),
    )


def test_batch_write_chunks_then_get_chunk(repo: Repo) -> None:
    project_id, document_id = _make_document(repo)
    chunk = _make_chunk(document_id, project_id, "chunk-1")

    repo.batch_write_chunks([chunk])

    fetched = repo.get_chunk(document_id, "chunk-1")
    assert fetched is not None
    assert fetched.text == chunk.text
    assert fetched.sentences[0].rects == [(72.0, 640.2, 511.4, 655.8)]


def test_batch_write_chunks_handles_more_than_25(repo: Repo) -> None:
    project_id, document_id = _make_document(repo)
    chunks = [_make_chunk(document_id, project_id, f"chunk-{i}") for i in range(30)]

    repo.batch_write_chunks(chunks)

    fetched = [repo.get_chunk(document_id, f"chunk-{i}") for i in range(30)]
    assert all(c is not None for c in fetched)


def test_batch_get_chunks_preserves_requested_order(repo: Repo) -> None:
    project_id, document_id = _make_document(repo)
    chunks = [_make_chunk(document_id, project_id, f"chunk-{i}") for i in range(3)]
    repo.batch_write_chunks(chunks)

    keys = [(document_id, "chunk-2"), (document_id, "chunk-0"), (document_id, "chunk-1")]
    fetched = repo.batch_get_chunks(keys)

    assert [c.chunk_id for c in fetched] == ["chunk-2", "chunk-0", "chunk-1"]


def test_batch_get_chunks_skips_missing_keys(repo: Repo) -> None:
    project_id, document_id = _make_document(repo)
    repo.batch_write_chunks([_make_chunk(document_id, project_id, "chunk-0")])

    fetched = repo.batch_get_chunks([(document_id, "chunk-0"), (document_id, "does-not-exist")])

    assert [c.chunk_id for c in fetched] == ["chunk-0"]


def test_list_chunks_returns_every_chunk_for_the_document(repo: Repo) -> None:
    project_id, document_id = _make_document(repo)
    other_document_id = "other-doc"
    chunks = [_make_chunk(document_id, project_id, f"chunk-{i}") for i in range(3)]
    repo.batch_write_chunks(chunks)
    repo.batch_write_chunks([_make_chunk(other_document_id, project_id, "chunk-0")])

    fetched = repo.list_chunks(document_id)

    assert {c.chunk_id for c in fetched} == {"chunk-0", "chunk-1", "chunk-2"}


def test_list_chunks_returns_empty_for_a_document_with_none(repo: Repo) -> None:
    _, document_id = _make_document(repo)
    assert repo.list_chunks(document_id) == []


def test_list_chunks_for_page_filters_to_only_that_page(repo: Repo) -> None:
    project_id, document_id = _make_document(repo)
    repo.batch_write_chunks(
        [
            _make_chunk(document_id, project_id, "p1-a", page_number=1),
            _make_chunk(document_id, project_id, "p1-b", page_number=1),
            _make_chunk(document_id, project_id, "p2-a", page_number=2),
        ]
    )

    fetched = repo.list_chunks_for_page(document_id, 1)

    assert {c.chunk_id for c in fetched} == {"p1-a", "p1-b"}


def test_list_chunks_for_page_returns_empty_for_a_page_with_none(repo: Repo) -> None:
    project_id, document_id = _make_document(repo)
    repo.batch_write_chunks([_make_chunk(document_id, project_id, "p1-a", page_number=1)])

    assert repo.list_chunks_for_page(document_id, 5) == []


def test_delete_pages_and_chunks_removes_only_that_document(repo: Repo) -> None:
    project_id, document_id = _make_document(repo)
    other_document_id = "other-doc"
    repo.put_page(
        Page(
            document_id=document_id,
            page_number=1,
            width=1.0,
            height=1.0,
            text_source="pdf",
            text_density=0.5,
        )
    )
    repo.batch_write_chunks([_make_chunk(document_id, project_id, "chunk-0")])
    repo.batch_write_chunks([_make_chunk(other_document_id, project_id, "chunk-0")])

    repo.delete_pages_and_chunks(document_id)

    assert repo.get_page(document_id, 1) is None
    assert repo.get_chunk(document_id, "chunk-0") is None
    assert repo.get_chunk(other_document_id, "chunk-0") is not None
    # The document's own META item must survive the sweep.
    assert repo.get_document(document_id) is not None
