"""Document route handlers' business logic (docs/05-api-contracts.md#documents)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from api import validation
from api.errors import bad_request, conflict
from common import authz
from common.config import Settings, get_settings
from common.models import Document, DocumentIngestion
from common.repo import Repo, new_id
from common.storage import DocumentsStore, render_key, source_key
from common.vectors import VectorIndexProtocol, chunk_vector_key, page_vector_key
from common.workflow import Workflow


def _expiry(settings: Settings) -> str:
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.presigned_url_ttl_seconds)
    return expires_at.strftime("%Y-%m-%dT%H:%M:%SZ")


def delete_storage(store: DocumentsStore, document: Document) -> None:
    store.delete_prefix(f"raw/{document.project_id}/{document.document_id}/")
    store.delete_prefix(f"pages/{document.project_id}/{document.document_id}/")
    store.delete_prefix(f"artifacts/{document.project_id}/{document.document_id}/")


def delete_vectors(repo: Repo, vectors: VectorIndexProtocol, document: Document) -> None:
    """docs/02-data-model.md#s3-vectors: "Deleting a document deletes its vectors by key (keys
    are deterministic, so no query is needed: page vectors from `pageCount`, chunk vectors from
    the chunk items)." Must run *before* `repo.delete_pages_and_chunks` — chunk ids are random
    ULIDs with no other source, so once the Chunk items are gone there is no way to recover
    which keys to delete. `delete_vectors_if_present` no-ops if the project's index was never
    created (a document that never got past `Probe`)."""
    keys = [
        chunk_vector_key(document.document_id, chunk.chunk_id)
        for chunk in repo.list_chunks(document.document_id)
    ]
    if document.page_count:
        keys.extend(
            page_vector_key(document.document_id, page_number)
            for page_number in range(1, document.page_count + 1)
        )
    index_name = get_settings().vector_index_name(document.project_id)
    vectors.delete_vectors_if_present(index_name, keys)


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
    repo: Repo,
    store: DocumentsStore,
    vectors: VectorIndexProtocol,
    owner_sub: str,
    project_id: str,
    document_id: str,
) -> dict[str, Any]:
    """docs/02-data-model.md's documented two-phase order: mark `DELETING`, delete vectors,
    delete S3 prefixes, delete chunk/page items, then delete the metadata — a crash after any
    step leaves orphan storage (cheap, sweepable) rather than dangling references."""
    document = authz.require_document(repo, owner_sub, project_id, document_id)
    repo.update_document(document_id, status="DELETING")
    delete_vectors(repo, vectors, document)
    delete_storage(store, document)
    repo.delete_pages_and_chunks(document_id)
    if document.ingestion.chunk_count:
        repo.increment_chunk_count(project_id, by=-document.ingestion.chunk_count)
    repo.delete_document(document)
    return {"status": "DELETING"}


def ingest(
    repo: Repo,
    store: DocumentsStore,
    workflow: Workflow,
    vectors: VectorIndexProtocol,
    owner_sub: str,
    project_id: str,
    document_id: str,
) -> dict[str, Any]:
    """docs/05-api-contracts.md#documents: "Ingest is idempotent and also serves as
    retry/re-index. It returns 409 if an execution is already running for that document." A
    re-ingest (status already `READY` or `FAILED`) deletes prior derived artifacts first —
    docs/03-ingestion.md's `MarkFailed` section: "the retry path is a full re-ingest, which
    deletes them first" — including reversing this document's earlier contribution to the
    project's `chunkCount`, since `ingest-finalize` will add the new count back once the fresh
    run completes. Vectors are deleted *here*, not inside the state machine's `EmbedAndIndex`
    step, because this is the last point the old chunk ids (needed to derive their vector keys)
    are still known — `repo.delete_pages_and_chunks` below deletes the Chunk items, and chunk
    ids are random ULIDs with no other source.
    """
    document = authz.require_document(repo, owner_sub, project_id, document_id)
    if document.status in ("PROCESSING", "DELETING"):
        raise conflict("INGEST_IN_PROGRESS", "An ingestion is already running for this document.")

    if document.status in ("READY", "FAILED"):
        delete_vectors(repo, vectors, document)
        store.delete_prefix(f"pages/{project_id}/{document_id}/")
        store.delete_prefix(f"artifacts/{project_id}/{document_id}/")
        repo.delete_pages_and_chunks(document_id)
        if document.ingestion.chunk_count:
            repo.increment_chunk_count(project_id, by=-document.ingestion.chunk_count)

    extension = validation.extension_for_content_type(document.content_type)
    key = source_key(project_id, document_id, extension)
    execution = workflow.start_execution(
        name=f"{document_id}-{new_id()}",
        input_payload={
            "projectId": project_id,
            "documentId": document_id,
            "s3Key": key,
            "contentType": document.content_type,
        },
    )
    repo.update_document(
        document_id,
        status="PROCESSING",
        status_detail=None,
        ingestion=DocumentIngestion(execution_arn=execution.execution_arn),
    )
    return {"executionArn": execution.execution_arn}


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


def page(
    repo: Repo, owner_sub: str, project_id: str, document_id: str, page_raw: str
) -> dict[str, Any]:
    """docs/05-api-contracts.md#documents: the Page item's coordinate-space metadata
    (`width`/`height`/`rotation`/`textSource`) — the piece docs/06-frontend.md's image-document
    viewer needs and that had no route at all before Phase 7 (a real gap: a citation's `rects`
    are only meaningful relative to the page they were extracted against, and there is no other
    way for the client to learn a page's canonical `width`/`height`, e.g. `page.getViewport()`
    doesn't exist for a standalone image document the way it does for a PDF page in pdf.js)."""
    authz.require_document(repo, owner_sub, project_id, document_id)
    page_number = validation.validate_page_number(page_raw)
    page_item = repo.get_page(document_id, page_number)
    if page_item is None:
        raise bad_request(
            "VALIDATION_ERROR",
            f"Page {page_number} does not exist for this document.",
        )
    return page_item.to_api()
