"""`scripts/cwd_reindex.py`'s pure orchestration (`reindex_project`) against fakes — the boto3
client construction in `main()` is intentionally untested here, same as any other adapter-
wiring `main()` in this codebase."""

from __future__ import annotations

from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws
from scripts.cwd_reindex import reindex_project

from common.config import get_settings
from common.models import Chunk, Page, PageRenders, Sentence
from common.repo import Repo
from common.storage import DocumentsStore, embed_render_key
from common.testing.embeddings import FakeNova
from common.testing.vectors import FakeVectorIndex

_BUCKET = "cwd-documents-test-reindex"


@pytest.fixture
def repo_and_store() -> Iterator[tuple[Repo, DocumentsStore]]:
    with mock_aws():
        settings = get_settings()
        dynamodb = boto3.client("dynamodb", region_name=settings.aws_region)
        dynamodb.create_table(
            TableName=settings.table_name,
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        s3 = boto3.client("s3", region_name=settings.aws_region)
        s3.create_bucket(Bucket=_BUCKET)
        yield (
            Repo(dynamodb, table_name=settings.table_name),
            DocumentsStore(s3, settings, bucket_name=_BUCKET),
        )


def test_reindex_project_embeds_chunks_and_pages_of_ready_documents(
    repo_and_store: tuple[Repo, DocumentsStore],
) -> None:
    repo, store = repo_and_store
    settings = get_settings()
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    document = repo.create_document(
        project_id=project.project_id,
        owner_sub="user-1",
        filename="a.pdf",
        content_type="application/pdf",
        byte_size=1,
        kind="pdf",
    )
    repo.update_document(document.document_id, status="READY", page_count=1)
    embed_key = embed_render_key(project.project_id, document.document_id, 1)
    store.put_object(embed_key, b"fake-jpeg-bytes", content_type="image/jpeg")
    repo.put_page(
        Page(
            document_id=document.document_id,
            page_number=1,
            width=612.0,
            height=792.0,
            text_source="pdf",
            text_density=0.5,
            s3=PageRenders(display="ignored", embed=embed_key),
        )
    )
    repo.batch_write_chunks(
        [
            Chunk(
                chunk_id="c1",
                document_id=document.document_id,
                project_id=project.project_id,
                page_number=1,
                ordinal=0,
                text="hello",
                sentences=[Sentence(i=0, text="hello", rects=[])],
                token_estimate=1,
                created_at="2026-01-01T00:00:00Z",
            )
        ]
    )

    vector_index = FakeVectorIndex()
    nova = FakeNova(settings)

    count = reindex_project(
        repo=repo,
        store=store,
        vector_index=vector_index,
        nova=nova,
        settings=settings,
        project_id=project.project_id,
    )

    assert count == 2  # one chunk vector, one page vector
    index_name = settings.vector_index_name(project.project_id)
    query_vector = nova.embed_text("hello", purpose="GENERIC_RETRIEVAL")
    matches = {m.key for m in vector_index.query(index_name, query_vector, top_k=10)}
    assert f"{document.document_id}:c1" in matches
    assert f"{document.document_id}:p0001" in matches


def test_reindex_project_skips_documents_that_are_not_ready(
    repo_and_store: tuple[Repo, DocumentsStore],
) -> None:
    repo, store = repo_and_store
    settings = get_settings()
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    repo.create_document(
        project_id=project.project_id,
        owner_sub="user-1",
        filename="a.pdf",
        content_type="application/pdf",
        byte_size=1,
        kind="pdf",
    )  # status defaults to PENDING

    count = reindex_project(
        repo=repo,
        store=store,
        vector_index=FakeVectorIndex(),
        nova=FakeNova(settings),
        settings=settings,
        project_id=project.project_id,
    )
    assert count == 0


def test_reindex_project_deletes_and_recreates_the_index(
    repo_and_store: tuple[Repo, DocumentsStore],
) -> None:
    repo, store = repo_and_store
    settings = get_settings()
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    vector_index = FakeVectorIndex()
    index_name = settings.vector_index_name(project.project_id)
    vector_index.create_index_if_missing(index_name, non_filterable_metadata_keys=[])
    stale_vector = [1.0] + [0.0] * (settings.embed_dim - 1)
    vector_index.put_vectors(index_name, [("stale:key", stale_vector, {})])

    reindex_project(
        repo=repo,
        store=store,
        vector_index=vector_index,
        nova=FakeNova(settings),
        settings=settings,
        project_id=project.project_id,
    )

    assert vector_index.query(index_name, stale_vector, top_k=10) == []
