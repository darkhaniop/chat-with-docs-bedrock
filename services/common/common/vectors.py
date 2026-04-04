"""S3 Vectors adapter — one index per project.

API surface confirmed live (`tests/contract/smoke_s3vectors.py`) against boto3's `s3vectors`
client: `create_vector_bucket`, `create_index`, `put_vectors`, `query_vectors`,
`delete_vectors`, `delete_index`, `delete_vector_bucket`.

`query_vectors`'s `filter` accepts plain equality (`{"documentId": "doc2"}`) and Mongo-style
operators (`{"documentId": {"$in": [...]}}`), confirmed live. `query_vectors` with
`returnMetadata=True` returns every metadata key including ones registered as non-filterable
(e.g. `preview`) — non-filterable only exempts a key from the filterable-metadata quota, it is
not withheld from responses.

`put_vectors`/`delete_vectors` are capped at 500 items per call (`client.meta.service_model`'s
`max` on the `vectors`/`keys` input members, confirmed live) — `put_vectors`/`delete_vectors`
below chunk internally so callers never have to think about the limit, the same way
`common.repo`'s `batch_write_chunks` chunks at DynamoDB's 25-item `BatchWriteItem` limit.

`create_index_if_missing`/`delete_index_if_present`/`delete_vectors_if_present` exist because
three call sites need genuinely idempotent behavior confirmed live (`smoke_s3vectors.py`):
`create_index` on a name that already exists raises `ConflictException`;
`delete_index`/`delete_vectors` on an index that does not exist raise `NotFoundException` (a
document/project that was never ingested has no index to clean up); `delete_vectors` on an index
that *does* exist, given keys that don't, is already a silent no-op with no exception — so only
the index-missing case needs catching.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any, Protocol

from common.config import Settings

# S3 Vectors' own limit on `PutVectors.vectors`/`DeleteVectors.keys`, confirmed live via
# `client.meta.service_model.operation_model(...).input_shape.members[...].metadata["max"]`.
_MAX_ITEMS_PER_CALL = 500


def chunk_vector_key(document_id: str, chunk_id: str) -> str:
    """`{documentId}:{chunkId}` for a text-kind vector. Pure key derivation, not I/O — lives
    here (not `services/ingestion`) so `api`'s document/project deletion paths can compute a
    document's vector keys without depending on the ingestion package for one function."""
    return f"{document_id}:{chunk_id}"


def page_vector_key(document_id: str, page_number: int) -> str:
    """`{documentId}:p{page:04d}` for a page-kind vector."""
    return f"{document_id}:p{page_number:04d}"


@dataclass(frozen=True)
class VectorMatch:
    key: str
    metadata: dict[str, Any]
    distance: float | None = None


class VectorIndexProtocol(Protocol):
    """The subset of `VectorIndex` ingestion/answering code depends on — mirrors
    `common.events.EventsPublisher`'s pattern so `common.testing.vectors.FakeVectorIndex` can
    structurally satisfy it without inheriting from the real (boto3-backed) class."""

    def create_index_if_missing(
        self, index_name: str, *, non_filterable_metadata_keys: list[str]
    ) -> None: ...

    def put_vectors(
        self, index_name: str, vectors: list[tuple[str, list[float], dict[str, Any]]]
    ) -> None: ...

    def query(
        self,
        index_name: str,
        query_vector: list[float],
        *,
        top_k: int,
        filter: dict[str, Any] | None = None,
    ) -> list[VectorMatch]: ...

    def delete_vectors_if_present(self, index_name: str, keys: list[str]) -> None: ...

    def delete_index_if_present(self, index_name: str) -> None: ...


class VectorIndex:
    def __init__(self, settings: Settings, client: Any, *, vector_bucket_name: str) -> None:
        self._settings = settings
        self._client = client
        self._bucket = vector_bucket_name

    def create_index(self, index_name: str, *, non_filterable_metadata_keys: list[str]) -> None:
        self._client.create_index(
            vectorBucketName=self._bucket,
            indexName=index_name,
            dataType=self._settings.vector_data_type,
            dimension=self._settings.embed_dim,
            distanceMetric=self._settings.vector_distance_metric,
            metadataConfiguration={"nonFilterableMetadataKeys": non_filterable_metadata_keys},
        )

    def create_index_if_missing(
        self, index_name: str, *, non_filterable_metadata_keys: list[str]
    ) -> None:
        """Ensure index: "Idempotently create ... on ConflictException/already-exists,
        continue." """
        with contextlib.suppress(self._client.exceptions.ConflictException):
            self.create_index(index_name, non_filterable_metadata_keys=non_filterable_metadata_keys)

    def put_vectors(
        self, index_name: str, vectors: list[tuple[str, list[float], dict[str, Any]]]
    ) -> None:
        for start in range(0, len(vectors), _MAX_ITEMS_PER_CALL):
            batch = vectors[start : start + _MAX_ITEMS_PER_CALL]
            self._client.put_vectors(
                vectorBucketName=self._bucket,
                indexName=index_name,
                vectors=[
                    {"key": key, "data": {"float32": values}, "metadata": metadata}
                    for key, values, metadata in batch
                ],
            )

    def query(
        self,
        index_name: str,
        query_vector: list[float],
        *,
        top_k: int,
        filter: dict[str, Any] | None = None,
    ) -> list[VectorMatch]:
        kwargs: dict[str, Any] = {
            "vectorBucketName": self._bucket,
            "indexName": index_name,
            "topK": top_k,
            "queryVector": {"float32": query_vector},
            "returnMetadata": True,
            "returnDistance": True,
        }
        if filter is not None:
            kwargs["filter"] = filter
        response = self._client.query_vectors(**kwargs)
        return [
            VectorMatch(key=v["key"], metadata=v.get("metadata", {}), distance=v.get("distance"))
            for v in response.get("vectors", [])
        ]

    def delete_vectors(self, index_name: str, keys: list[str]) -> None:
        for start in range(0, len(keys), _MAX_ITEMS_PER_CALL):
            batch = keys[start : start + _MAX_ITEMS_PER_CALL]
            self._client.delete_vectors(
                vectorBucketName=self._bucket, indexName=index_name, keys=batch
            )

    def delete_vectors_if_present(self, index_name: str, keys: list[str]) -> None:
        """Swallows `NotFoundException` for the *index-missing* case only — a document/project
        that was never ingested (or was never ingested past `EnsureIndex`) has no index to
        delete from. `delete_vectors` on an existing index given nonexistent keys is already a
        silent no-op (confirmed live), so this only needs to guard the index itself."""
        if not keys:
            return
        with contextlib.suppress(self._client.exceptions.NotFoundException):
            self.delete_vectors(index_name, keys)

    def delete_index(self, index_name: str) -> None:
        self._client.delete_index(vectorBucketName=self._bucket, indexName=index_name)

    def delete_index_if_present(self, index_name: str) -> None:
        with contextlib.suppress(self._client.exceptions.NotFoundException):
            self.delete_index(index_name)
