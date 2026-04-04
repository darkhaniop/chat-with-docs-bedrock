"""`uv run cwd-reindex --project <projectId>` - regenerate a project's S3 Vectors index from
DynamoDB chunks + S3 page renders.

`reindex_project` is the pure orchestration (adapter objects passed in, so it's unit-testable
against fakes - see `services/common/tests/test_cwd_reindex.py`).
"""

from __future__ import annotations

import argparse

import boto3

from common.bedrock.embeddings import NovaEmbeddings, NovaEmbeddingsProtocol
from common.config import Settings, get_settings
from common.repo import Repo
from common.storage import DocumentsStore
from common.vectors import VectorIndex, VectorIndexProtocol
from ingestion.embed import embed_chunks, embed_pages


def reindex_project(
    *,
    repo: Repo,
    store: DocumentsStore,
    vector_index: VectorIndexProtocol,
    nova: NovaEmbeddingsProtocol,
    settings: Settings,
    project_id: str,
) -> int:
    """Drops and recreates the project's index, then re-embeds every `READY` document's chunks
    and page renders. Returns the number of vectors written."""
    index_name = settings.vector_index_name(project_id)
    vector_index.delete_index_if_present(index_name)
    vector_index.create_index_if_missing(index_name, non_filterable_metadata_keys=["preview"])

    total = 0
    for document in repo.list_all_documents(project_id):
        if document.status != "READY":
            continue
        chunks = repo.list_chunks(document.document_id)
        pages = repo.list_pages(document.document_id)
        page_jobs = [
            (page, store.get_object(page.s3.embed)) for page in pages if page.s3 is not None
        ]
        vectors = embed_chunks(nova, chunks, max_concurrency=settings.embed_max_concurrency)
        vectors += embed_pages(nova, page_jobs, max_concurrency=settings.embed_max_concurrency)
        if vectors:
            vector_index.put_vectors(index_name, vectors)
        total += len(vectors)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenerate a project's S3 Vectors index from DynamoDB + S3."
    )
    parser.add_argument("--project", dest="project_id", required=True, help="Project id")
    args = parser.parse_args()

    settings = get_settings()
    account_id = boto3.client("sts").get_caller_identity()["Account"]

    dynamodb = boto3.client("dynamodb", region_name=settings.aws_region)
    repo = Repo(dynamodb, table_name=settings.table_name)

    s3 = boto3.client("s3", region_name=settings.aws_region)
    store = DocumentsStore(s3, settings, bucket_name=settings.documents_bucket_name(account_id))

    s3vectors = boto3.client("s3vectors", region_name=settings.aws_region)
    vector_index = VectorIndex(
        settings, s3vectors, vector_bucket_name=settings.vector_bucket_name(account_id)
    )

    bedrock_runtime = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    nova = NovaEmbeddings(settings, bedrock_runtime)

    count = reindex_project(
        repo=repo,
        store=store,
        vector_index=vector_index,
        nova=nova,
        settings=settings,
        project_id=args.project_id,
    )
    print(f"Reindexed project {args.project_id}: {count} vectors written.")


if __name__ == "__main__":
    main()
