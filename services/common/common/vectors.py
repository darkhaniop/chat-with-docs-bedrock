"""S3 Vectors adapter — one index per project.

API surface confirmed live (`tests/contract/smoke_s3vectors.py`) against boto3's `s3vectors`
client: `create_vector_bucket`, `create_index`, `put_vectors`, `query_vectors`,
`delete_vectors`, `delete_index`, `delete_vector_bucket`.

`query_vectors`'s `filter` accepts plain equality (`{"documentId": "doc2"}`) and Mongo-style
operators (`{"documentId": {"$in": [...]}}`), confirmed live. `query_vectors` with
`returnMetadata=True` returns every metadata key including ones registered as non-filterable
(e.g. `preview`) — non-filterable only exempts a key from the filterable-metadata quota, it is
not withheld from responses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common.config import Settings


@dataclass(frozen=True)
class VectorMatch:
    key: str
    metadata: dict[str, Any]
    distance: float | None = None


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

    def put_vectors(
        self, index_name: str, vectors: list[tuple[str, list[float], dict[str, Any]]]
    ) -> None:
        self._client.put_vectors(
            vectorBucketName=self._bucket,
            indexName=index_name,
            vectors=[
                {"key": key, "data": {"float32": values}, "metadata": metadata}
                for key, values, metadata in vectors
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
        self._client.delete_vectors(vectorBucketName=self._bucket, indexName=index_name, keys=keys)

    def delete_index(self, index_name: str) -> None:
        self._client.delete_index(vectorBucketName=self._bucket, indexName=index_name)
