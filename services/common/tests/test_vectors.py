"""`common.vectors.VectorIndex` behavior that doesn't need real AWS: batching at the 500-item
limit and the three idempotent wrappers (`tests/contract/smoke_s3vectors.py` pins the live
exception shapes these wrappers catch)."""

from __future__ import annotations

from typing import Any

import pytest

from common.config import get_settings
from common.vectors import VectorIndex


class _FakeConflictError(Exception):
    pass


class _FakeNotFoundError(Exception):
    pass


class _FakeExceptions:
    ConflictException = _FakeConflictError
    NotFoundException = _FakeNotFoundError


class _RecordingClient:
    """Records every `put_vectors`/`delete_vectors` call's item count; `create_index`/
    `delete_index`/`delete_vectors` optionally raise on request, mirroring the live shapes."""

    def __init__(self) -> None:
        self.exceptions = _FakeExceptions()
        self.put_vectors_calls: list[int] = []
        self.delete_vectors_calls: list[int] = []
        self.create_index_calls = 0
        self.delete_index_calls = 0
        self.raise_on_create_index: Exception | None = None
        self.raise_on_delete_index: Exception | None = None
        self.raise_on_delete_vectors: Exception | None = None

    def create_index(self, **kwargs: Any) -> None:
        self.create_index_calls += 1
        if self.raise_on_create_index is not None:
            raise self.raise_on_create_index

    def put_vectors(self, **kwargs: Any) -> None:
        self.put_vectors_calls.append(len(kwargs["vectors"]))

    def delete_vectors(self, **kwargs: Any) -> None:
        if self.raise_on_delete_vectors is not None:
            raise self.raise_on_delete_vectors
        self.delete_vectors_calls.append(len(kwargs["keys"]))

    def delete_index(self, **kwargs: Any) -> None:
        self.delete_index_calls += 1
        if self.raise_on_delete_index is not None:
            raise self.raise_on_delete_index


@pytest.fixture
def index() -> tuple[VectorIndex, _RecordingClient]:
    settings = get_settings()
    client = _RecordingClient()
    return VectorIndex(settings, client, vector_bucket_name="bucket"), client


def test_put_vectors_batches_at_500(index: tuple[VectorIndex, _RecordingClient]) -> None:
    vector_index, client = index
    vectors: list[tuple[str, list[float], dict[str, Any]]] = [
        (f"k{i}", [0.0] * 8, {}) for i in range(1201)
    ]
    vector_index.put_vectors("idx", vectors)
    assert client.put_vectors_calls == [500, 500, 201]


def test_delete_vectors_batches_at_500(index: tuple[VectorIndex, _RecordingClient]) -> None:
    vector_index, client = index
    keys = [f"k{i}" for i in range(501)]
    vector_index.delete_vectors("idx", keys)
    assert client.delete_vectors_calls == [500, 1]


def test_create_index_if_missing_swallows_conflict(
    index: tuple[VectorIndex, _RecordingClient],
) -> None:
    vector_index, client = index
    client.raise_on_create_index = _FakeConflictError()
    vector_index.create_index_if_missing("idx", non_filterable_metadata_keys=["preview"])
    assert client.create_index_calls == 1


def test_create_index_if_missing_reraises_other_errors(
    index: tuple[VectorIndex, _RecordingClient],
) -> None:
    vector_index, client = index
    client.raise_on_create_index = ValueError("not a conflict")
    with pytest.raises(ValueError, match="not a conflict"):
        vector_index.create_index_if_missing("idx", non_filterable_metadata_keys=[])


def test_delete_index_if_present_swallows_not_found(
    index: tuple[VectorIndex, _RecordingClient],
) -> None:
    vector_index, client = index
    client.raise_on_delete_index = _FakeNotFoundError()
    vector_index.delete_index_if_present("idx")
    assert client.delete_index_calls == 1


def test_delete_vectors_if_present_swallows_not_found(
    index: tuple[VectorIndex, _RecordingClient],
) -> None:
    vector_index, client = index
    client.raise_on_delete_vectors = _FakeNotFoundError()
    vector_index.delete_vectors_if_present("idx", ["a:b"])  # must not raise


def test_delete_vectors_if_present_is_a_no_op_for_an_empty_key_list(
    index: tuple[VectorIndex, _RecordingClient],
) -> None:
    vector_index, client = index
    vector_index.delete_vectors_if_present("idx", [])
    assert client.delete_vectors_calls == []
