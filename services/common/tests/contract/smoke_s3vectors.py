"""Pin the S3 Vectors API contract.

Creates a throwaway vector bucket and index, exercises the full lifecycle, and deletes
everything it created, whether or not the assertions pass.

- Operations: `create_vector_bucket`, `create_index`, `put_vectors`, `query_vectors`,
  `delete_vectors`, `delete_index`, `delete_vector_bucket` (plus bucket/index get/list, tagging,
  and bucket policy operations not used here).
- `create_index` takes `vectorBucketName`, `indexName`, `dataType` ("float32"), `dimension`,
  `distanceMetric` ("cosine"), and `metadataConfiguration.nonFilterableMetadataKeys`.
- `put_vectors` takes `vectors: [{"key", "data": {"float32": [...]}, "metadata": {...}}]`.
- `query_vectors`'s `filter` accepts plain equality (`{"documentId": "doc2"}`) *and*
  Mongo-style operators (`{"documentId": {"$in": ["doc1"]}}`) — both confirmed live.
- `query_vectors` with `returnMetadata=True` returns every metadata key, including ones
  registered as non-filterable (`preview`); non-filterable only exempts a key from the
  filterable-metadata quota, it is not withheld from the response.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from common.config import Settings
from common.vectors import VectorIndex

pytestmark = pytest.mark.contract

_BUCKET = "cwd-smoke-test-vectors"
_INDEX = "smoke-index"
_DIM = 8


@pytest.fixture
def vector_bucket(s3vectors: object) -> Iterator[None]:
    s3vectors.create_vector_bucket(vectorBucketName=_BUCKET)  # type: ignore[attr-defined]
    try:
        yield
    finally:
        s3vectors.delete_vector_bucket(vectorBucketName=_BUCKET)  # type: ignore[attr-defined]


@pytest.fixture
def index(settings: Settings, s3vectors: object, vector_bucket: None) -> Iterator[VectorIndex]:
    scoped = settings.model_copy(update={"embed_dim": _DIM})
    vector_index = VectorIndex(scoped, s3vectors, vector_bucket_name=_BUCKET)
    vector_index.create_index(_INDEX, non_filterable_metadata_keys=["preview"])
    try:
        yield vector_index
    finally:
        vector_index.delete_index(_INDEX)


def test_put_query_filter_and_delete_roundtrip(index: VectorIndex) -> None:
    index.put_vectors(
        _INDEX,
        [
            (
                "doc1:chunk1",
                [1.0, 0, 0, 0, 0, 0, 0, 0],
                {"documentId": "doc1", "kind": "text", "preview": "hello world"},
            ),
            (
                "doc1:chunk2",
                [0, 1.0, 0, 0, 0, 0, 0, 0],
                {"documentId": "doc1", "kind": "text", "preview": "second chunk"},
            ),
            (
                "doc2:p0001",
                [0, 0, 1.0, 0, 0, 0, 0, 0],
                {"documentId": "doc2", "kind": "page", "preview": "page image"},
            ),
        ],
    )

    unfiltered = index.query(_INDEX, [1.0, 0, 0, 0, 0, 0, 0, 0], top_k=3)
    assert {m.key for m in unfiltered} == {"doc1:chunk1", "doc1:chunk2", "doc2:p0001"}
    assert unfiltered[0].key == "doc1:chunk1"  # exact match, distance 0

    equality_filtered = index.query(
        _INDEX, [1.0, 0, 0, 0, 0, 0, 0, 0], top_k=3, filter={"documentId": "doc2"}
    )
    assert [m.key for m in equality_filtered] == ["doc2:p0001"]

    in_filtered = index.query(
        _INDEX,
        [1.0, 0, 0, 0, 0, 0, 0, 0],
        top_k=3,
        filter={"documentId": {"$in": ["doc1"]}},
    )
    assert {m.key for m in in_filtered} == {"doc1:chunk1", "doc1:chunk2"}

    index.delete_vectors(_INDEX, ["doc1:chunk1", "doc1:chunk2", "doc2:p0001"])
    remaining = index.query(_INDEX, [1.0, 0, 0, 0, 0, 0, 0, 0], top_k=3)
    assert remaining == []
