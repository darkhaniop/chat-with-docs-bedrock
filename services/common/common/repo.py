"""DynamoDB access for Project, Document, Page, and Chunk (docs/02-data-model.md).

Conversation and Message repo functions arrive in Phase 5, the phase that first writes those
items — building CRUD for entities nothing produces yet is dead, untested code.

Takes a low-level `dynamodb` boto3 client (not the higher-level resource) because
`transact_write_items` — needed to keep a canonical item and its list-view copy consistent
(docs/02-data-model.md#write-patterns-worth-calling-out) — is only on the client. One
serializer/deserializer pair converts between plain Python dicts and DynamoDB's AttributeValue
wire format everywhere, so the rest of this module never touches `{"S": ...}` shapes directly.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from ulid import ULID

from common.models import Chunk, Document, DocumentKind, Page, Project, ProjectSummary

_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


def _floats_to_decimal(value: Any) -> Any:
    """DynamoDB's `TypeSerializer` rejects a bare Python `float` ("Float types are not
    supported. Use Decimal types instead.") — only `Project`/`Document` fields predate this
    module needing it, since neither has a float field; `Page`/`Chunk` geometry (width, height,
    rects) does. Converting via `str()` avoids the binary-float rounding a direct
    `Decimal(0.1)` would otherwise bake in."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _floats_to_decimal(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_floats_to_decimal(v) for v in value]
    return value


def _decimals_to_float(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _decimals_to_float(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decimals_to_float(v) for v in value]
    return value


class NotFound(Exception):  # noqa: N818 — name mandated by docs/07-security.md's own code sample
    """Get methods return `None`, never raise, on a missing item — this is raised by mutating
    methods that need an item to already exist, and reused by `authz.py` for the 404-on-wrong-
    owner case, so callers only need to catch one exception type."""


def new_id() -> str:
    return str(ULID())


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ser(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _serializer.serialize(_floats_to_decimal(v)) for k, v in item.items()}


def _deser(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _decimals_to_float(_deserializer.deserialize(v)) for k, v in item.items()}


def encode_cursor(key: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(key).encode()).decode()


def decode_cursor(cursor: str) -> dict[str, Any]:
    return dict(json.loads(base64.urlsafe_b64decode(cursor.encode())))


class Repo:
    def __init__(self, client: Any, *, table_name: str) -> None:
        self._client = client
        self._table = table_name

    # -- Project -----------------------------------------------------------------------------

    def create_project(self, *, owner_sub: str, name: str, description: str) -> Project:
        now = now_iso()
        project = Project(
            project_id=new_id(),
            owner_sub=owner_sub,
            name=name,
            description=description,
            document_count=0,
            chunk_count=0,
            created_at=now,
            updated_at=now,
        )
        canonical = {
            "pk": f"PROJECT#{project.project_id}",
            "sk": "META",
            "entity": "Project",
            **project.model_dump(by_alias=True),
        }
        summary = ProjectSummary(
            project_id=project.project_id,
            name=project.name,
            document_count=0,
            updated_at=now,
        )
        list_view = {
            "pk": f"USER#{owner_sub}",
            "sk": f"PROJECT#{project.project_id}",
            "entity": "Project",
            **summary.model_dump(by_alias=True),
        }
        self._client.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": self._table,
                        "Item": _ser(canonical),
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }
                },
                {
                    "Put": {
                        "TableName": self._table,
                        "Item": _ser(list_view),
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }
                },
            ]
        )
        return project

    def get_project(self, project_id: str) -> Project | None:
        response = self._client.get_item(
            TableName=self._table,
            Key=_ser({"pk": f"PROJECT#{project_id}", "sk": "META"}),
        )
        item = response.get("Item")
        return None if item is None else Project.model_validate(_deser(item))

    def list_projects(
        self, owner_sub: str, *, limit: int, cursor: str | None
    ) -> tuple[list[ProjectSummary], str | None]:
        kwargs: dict[str, Any] = {
            "TableName": self._table,
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :prefix)",
            "ExpressionAttributeValues": _ser({":pk": f"USER#{owner_sub}", ":prefix": "PROJECT#"}),
            "Limit": limit,
        }
        if cursor is not None:
            kwargs["ExclusiveStartKey"] = _ser(decode_cursor(cursor))
        response = self._client.query(**kwargs)
        items = [ProjectSummary.model_validate(_deser(i)) for i in response.get("Items", [])]
        last_key = response.get("LastEvaluatedKey")
        next_cursor = encode_cursor(_deser(last_key)) if last_key else None
        return items, next_cursor

    def update_project(
        self, project_id: str, owner_sub: str, *, name: str | None, description: str | None
    ) -> Project:
        project = self.get_project(project_id)
        if project is None:
            raise NotFound(project_id)
        now = now_iso()
        updated = project.model_copy(
            update={
                "name": name if name is not None else project.name,
                "description": description if description is not None else project.description,
                "updated_at": now,
            }
        )
        canonical = {
            "pk": f"PROJECT#{project_id}",
            "sk": "META",
            "entity": "Project",
            **updated.model_dump(by_alias=True),
        }
        summary = ProjectSummary(
            project_id=project_id,
            name=updated.name,
            document_count=updated.document_count,
            updated_at=now,
        )
        list_view = {
            "pk": f"USER#{owner_sub}",
            "sk": f"PROJECT#{project_id}",
            "entity": "Project",
            **summary.model_dump(by_alias=True),
        }
        self._client.transact_write_items(
            TransactItems=[
                {"Put": {"TableName": self._table, "Item": _ser(canonical)}},
                {"Put": {"TableName": self._table, "Item": _ser(list_view)}},
            ]
        )
        return updated

    def delete_project(self, project_id: str, owner_sub: str) -> None:
        """Deletes only the project's own two items. Cascading document/S3 cleanup is the
        caller's job (docs/02-data-model.md's two-phase deletion order lives in the service
        layer, which has the S3 adapter this module deliberately does not depend on)."""
        self._client.transact_write_items(
            TransactItems=[
                {
                    "Delete": {
                        "TableName": self._table,
                        "Key": _ser({"pk": f"PROJECT#{project_id}", "sk": "META"}),
                    }
                },
                {
                    "Delete": {
                        "TableName": self._table,
                        "Key": _ser({"pk": f"USER#{owner_sub}", "sk": f"PROJECT#{project_id}"}),
                    }
                },
            ]
        )

    def increment_chunk_count(self, project_id: str, *, by: int) -> None:
        """`chunkCount` lives only on the canonical `Project` item, not the `USER#` list-view
        copy (docs/02-data-model.md#project: the summary carries only `{projectId, name,
        documentCount, updatedAt}`), so — unlike `increment_document_count` — this is a single
        `UpdateItem`, not a transaction."""
        self._client.update_item(
            TableName=self._table,
            Key=_ser({"pk": f"PROJECT#{project_id}", "sk": "META"}),
            UpdateExpression="SET chunkCount = chunkCount + :by, updatedAt = :now",
            ExpressionAttributeValues=_ser({":by": by, ":now": now_iso()}),
        )

    def increment_document_count(self, project_id: str, owner_sub: str, *, by: int) -> None:
        now = now_iso()
        self._client.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": self._table,
                        "Key": _ser({"pk": f"PROJECT#{project_id}", "sk": "META"}),
                        "UpdateExpression": (
                            "SET documentCount = documentCount + :by, updatedAt = :now"
                        ),
                        "ExpressionAttributeValues": _ser({":by": by, ":now": now}),
                    }
                },
                {
                    "Update": {
                        "TableName": self._table,
                        "Key": _ser({"pk": f"USER#{owner_sub}", "sk": f"PROJECT#{project_id}"}),
                        "UpdateExpression": (
                            "SET documentCount = documentCount + :by, updatedAt = :now"
                        ),
                        "ExpressionAttributeValues": _ser({":by": by, ":now": now}),
                    }
                },
            ]
        )

    # -- Document ------------------------------------------------------------------------------

    def _put_document(self, document: Document) -> None:
        item = document.model_dump(by_alias=True)
        canonical = {
            "pk": f"DOC#{document.document_id}",
            "sk": "META",
            "entity": "Document",
            **item,
        }
        list_view = {
            "pk": f"PROJECT#{document.project_id}",
            "sk": f"DOC#{document.document_id}",
            "entity": "Document",
            **item,
        }
        self._client.transact_write_items(
            TransactItems=[
                {"Put": {"TableName": self._table, "Item": _ser(canonical)}},
                {"Put": {"TableName": self._table, "Item": _ser(list_view)}},
            ]
        )

    def create_document(
        self,
        *,
        project_id: str,
        owner_sub: str,
        filename: str,
        content_type: str,
        byte_size: int,
        kind: DocumentKind,
    ) -> Document:
        now = now_iso()
        document = Document(
            document_id=new_id(),
            project_id=project_id,
            owner_sub=owner_sub,
            filename=filename,
            content_type=content_type,
            byte_size=byte_size,
            kind=kind,
            status="PENDING",
            created_at=now,
            updated_at=now,
        )
        self._put_document(document)
        self.increment_document_count(project_id, owner_sub, by=1)
        return document

    def get_document(self, document_id: str) -> Document | None:
        response = self._client.get_item(
            TableName=self._table,
            Key=_ser({"pk": f"DOC#{document_id}", "sk": "META"}),
        )
        item = response.get("Item")
        return None if item is None else Document.model_validate(_deser(item))

    def list_documents(
        self, project_id: str, *, limit: int, cursor: str | None
    ) -> tuple[list[Document], str | None]:
        kwargs: dict[str, Any] = {
            "TableName": self._table,
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :prefix)",
            "ExpressionAttributeValues": _ser({":pk": f"PROJECT#{project_id}", ":prefix": "DOC#"}),
            "Limit": limit,
        }
        if cursor is not None:
            kwargs["ExclusiveStartKey"] = _ser(decode_cursor(cursor))
        response = self._client.query(**kwargs)
        items = [Document.model_validate(_deser(i)) for i in response.get("Items", [])]
        last_key = response.get("LastEvaluatedKey")
        next_cursor = encode_cursor(_deser(last_key)) if last_key else None
        return items, next_cursor

    def list_all_documents(self, project_id: str) -> list[Document]:
        """Unpaginated — used by project-deletion cascade, never by an API route."""
        documents: list[Document] = []
        cursor: str | None = None
        while True:
            page, cursor = self.list_documents(project_id, limit=100, cursor=cursor)
            documents.extend(page)
            if cursor is None:
                return documents

    def update_document(self, document_id: str, **fields: Any) -> Document:
        document = self.get_document(document_id)
        if document is None:
            raise NotFound(document_id)
        updated = document.model_copy(update={**fields, "updated_at": now_iso()})
        self._put_document(updated)
        return updated

    def update_document_ingestion(self, document_id: str, **fields: Any) -> Document:
        """Merges `fields` into the existing `ingestion` sub-object rather than replacing it
        wholesale, so a later pipeline step (e.g. `ingest-finalize` setting `finishedAt`) can
        never silently wipe out what an earlier one (e.g. the API's `:ingest` handler setting
        `executionArn`) already wrote. Every ingestion pipeline step should call this instead of
        `update_document(document_id, ingestion=...)` directly."""
        document = self.get_document(document_id)
        if document is None:
            raise NotFound(document_id)
        updated_ingestion = document.ingestion.model_copy(update=fields)
        return self.update_document(document_id, ingestion=updated_ingestion)

    def delete_document(self, document: Document) -> None:
        self._client.transact_write_items(
            TransactItems=[
                {
                    "Delete": {
                        "TableName": self._table,
                        "Key": _ser({"pk": f"DOC#{document.document_id}", "sk": "META"}),
                    }
                },
                {
                    "Delete": {
                        "TableName": self._table,
                        "Key": _ser(
                            {
                                "pk": f"PROJECT#{document.project_id}",
                                "sk": f"DOC#{document.document_id}",
                            }
                        ),
                    }
                },
            ]
        )
        self.increment_document_count(document.project_id, document.owner_sub, by=-1)

    def scan_stale_pending_documents(self, *, older_than_iso: str) -> list[Document]:
        """Full-table scan filtered to `entity = Document AND status = PENDING AND createdAt <
        :cutoff`. Deliberately a scan, not a query — this is the daily orphan-upload sweeper
        (docs/02-data-model.md#s3-layout), not a request-path access pattern, and adding a GSI
        for a once-a-day maintenance job would be exactly the speculative index the data model
        doc warns against."""
        documents: list[Document] = []
        kwargs: dict[str, Any] = {
            "TableName": self._table,
            # `sk = META` restricts the scan to canonical items — the list-view copy under
            # `PROJECT#{p}` mirrors the same `entity`/`status`/`createdAt` fields and would
            # otherwise be counted (and swept) twice.
            "FilterExpression": (
                "begins_with(pk, :prefix) AND sk = :meta AND entity = :entity"
                " AND #s = :status AND createdAt < :cutoff"
            ),
            "ExpressionAttributeNames": {"#s": "status"},
            "ExpressionAttributeValues": _ser(
                {
                    ":prefix": "DOC#",
                    ":meta": "META",
                    ":entity": "Document",
                    ":status": "PENDING",
                    ":cutoff": older_than_iso,
                }
            ),
        }
        while True:
            response = self._client.scan(**kwargs)
            documents.extend(Document.model_validate(_deser(i)) for i in response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return documents
            kwargs["ExclusiveStartKey"] = last_key

    # -- Page (docs/02-data-model.md#page) ------------------------------------------------------

    def put_page(self, page: Page) -> None:
        """A single item, no list-view copy — access pattern 5 ("list a document's pages") is
        satisfied by the main index alone. Used both to create the page at Probe and to update
        it with render keys at `ingest-page`; the caller passes the full desired item either
        way, DynamoDB's `PutItem` replaces wholesale."""
        item = {
            "pk": f"DOC#{page.document_id}",
            "sk": f"PAGE#{page.page_number:04d}",
            "entity": "Page",
            **page.model_dump(by_alias=True),
        }
        self._client.put_item(TableName=self._table, Item=_ser(item))

    def get_page(self, document_id: str, page_number: int) -> Page | None:
        response = self._client.get_item(
            TableName=self._table,
            Key=_ser({"pk": f"DOC#{document_id}", "sk": f"PAGE#{page_number:04d}"}),
        )
        item = response.get("Item")
        return None if item is None else Page.model_validate(_deser(item))

    def list_pages(self, document_id: str) -> list[Page]:
        """Unpaginated — bounded by `max_document_pages` (1000), well under a single `Query`'s
        1 MB response limit for items this small."""
        pages: list[Page] = []
        kwargs: dict[str, Any] = {
            "TableName": self._table,
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :prefix)",
            "ExpressionAttributeValues": _ser({":pk": f"DOC#{document_id}", ":prefix": "PAGE#"}),
        }
        while True:
            response = self._client.query(**kwargs)
            pages.extend(Page.model_validate(_deser(i)) for i in response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return pages
            kwargs["ExclusiveStartKey"] = last_key

    # -- Chunk — the citation map (docs/02-data-model.md#chunk--the-citation-map) ---------------

    def batch_write_chunks(self, chunks: list[Chunk]) -> None:
        """`BatchWriteItem` in batches of 25 with retry on unprocessed items
        (docs/02-data-model.md#write-patterns-worth-calling-out). Deterministic chunk ids make
        this idempotent, so a retried batch after a partial failure is always safe."""
        for start in range(0, len(chunks), 25):
            batch = chunks[start : start + 25]
            request_items = {
                self._table: [
                    {
                        "PutRequest": {
                            "Item": _ser(
                                {
                                    "pk": f"DOC#{chunk.document_id}",
                                    "sk": f"CHUNK#{chunk.chunk_id}",
                                    "entity": "Chunk",
                                    **chunk.model_dump(by_alias=True),
                                }
                            )
                        }
                    }
                    for chunk in batch
                ]
            }
            while request_items:
                response = self._client.batch_write_item(RequestItems=request_items)
                request_items = response.get("UnprocessedItems") or {}

    def get_chunk(self, document_id: str, chunk_id: str) -> Chunk | None:
        response = self._client.get_item(
            TableName=self._table,
            Key=_ser({"pk": f"DOC#{document_id}", "sk": f"CHUNK#{chunk_id}"}),
        )
        item = response.get("Item")
        return None if item is None else Chunk.model_validate(_deser(item))

    def batch_get_chunks(self, keys: list[tuple[str, str]]) -> list[Chunk]:
        """`keys` is `[(documentId, chunkId), ...]` — access pattern 6, the read side of the
        citation map. `BatchGetItem` caps at 100 keys per call and does not preserve order, so
        results are re-ordered to match the caller's `keys` (chunk hydration order matters:
        docs/04-retrieval-and-citations.md#ordering)."""
        chunks_by_key: dict[tuple[str, str], Chunk] = {}
        for start in range(0, len(keys), 100):
            batch = keys[start : start + 100]
            request_keys = [
                _ser({"pk": f"DOC#{document_id}", "sk": f"CHUNK#{chunk_id}"})
                for document_id, chunk_id in batch
            ]
            request_items = {self._table: {"Keys": request_keys}}
            while request_items:
                response = self._client.batch_get_item(RequestItems=request_items)
                for item in response.get("Responses", {}).get(self._table, []):
                    chunk = Chunk.model_validate(_deser(item))
                    chunks_by_key[(chunk.document_id, chunk.chunk_id)] = chunk
                request_items = response.get("UnprocessedKeys") or {}
        return [chunks_by_key[key] for key in keys if key in chunks_by_key]

    def list_chunks(self, document_id: str) -> list[Chunk]:
        """Unpaginated — used by the integration test that verifies a hand-checked sentence
        rect end to end (no HTTP route exposes chunks yet; that's Phase 5's answering path,
        which reads them via `batch_get_chunks` from known ids, not a listing)."""
        chunks: list[Chunk] = []
        kwargs: dict[str, Any] = {
            "TableName": self._table,
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :prefix)",
            "ExpressionAttributeValues": _ser({":pk": f"DOC#{document_id}", ":prefix": "CHUNK#"}),
        }
        while True:
            response = self._client.query(**kwargs)
            chunks.extend(Chunk.model_validate(_deser(i)) for i in response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return chunks
            kwargs["ExclusiveStartKey"] = last_key

    def delete_pages_and_chunks(self, document_id: str) -> None:
        """Query-and-batch-delete every `PAGE#`/`CHUNK#` item under a document
        (docs/02-data-model.md#write-patterns-worth-calling-out's document-deletion order, and
        the re-ingest path in docs/03-ingestion.md's `MarkFailed` section: "the retry path is a
        full re-ingest, which deletes them first"). One query since both sort-key prefixes share
        the same partition; no `entity` filter needed because `DOC#{id}/META` is the only other
        item on this partition and it never matches `begins_with(sk, "PAGE#"|"CHUNK#")`.
        """
        keys_to_delete: list[dict[str, Any]] = []
        for prefix in ("PAGE#", "CHUNK#"):
            kwargs: dict[str, Any] = {
                "TableName": self._table,
                "KeyConditionExpression": "pk = :pk AND begins_with(sk, :prefix)",
                "ExpressionAttributeValues": _ser({":pk": f"DOC#{document_id}", ":prefix": prefix}),
                "ProjectionExpression": "pk, sk",
            }
            while True:
                response = self._client.query(**kwargs)
                keys_to_delete.extend(_deser(i) for i in response.get("Items", []))
                last_key = response.get("LastEvaluatedKey")
                if not last_key:
                    break
                kwargs["ExclusiveStartKey"] = last_key

        for start in range(0, len(keys_to_delete), 25):
            batch = keys_to_delete[start : start + 25]
            request_items = {self._table: [{"DeleteRequest": {"Key": _ser(key)}} for key in batch]}
            while request_items:
                response = self._client.batch_write_item(RequestItems=request_items)
                request_items = response.get("UnprocessedItems") or {}
