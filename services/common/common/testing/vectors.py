"""`FakeVectorIndex` — docs/08-testing.md: "in-memory dict with real metadata filtering and
exact cosine search. Good enough to test fusion and filtering logic." Structurally satisfies
`common.vectors.VectorIndexProtocol`; unlike the real `VectorIndex` it never raises on a missing
index/duplicate create — the *_if_missing/_if_present methods are the only ones ingestion and
answering code actually calls, so that's the only behavior worth reproducing here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from common.vectors import VectorMatch


@dataclass(frozen=True)
class _StoredVector:
    key: str
    values: list[float]
    metadata: dict[str, Any]


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 1.0
    return 1.0 - dot / (norm_a * norm_b)


def _matches_filter(metadata: dict[str, Any], filter: dict[str, Any] | None) -> bool:
    """Mirrors the two forms confirmed live in `tests/contract/smoke_s3vectors.py`: plain
    equality and Mongo-style `{"$in": [...]}`."""
    if filter is None:
        return True
    for key, condition in filter.items():
        value = metadata.get(key)
        if isinstance(condition, dict) and "$in" in condition:
            if value not in condition["$in"]:
                return False
        elif value != condition:
            return False
    return True


@dataclass
class FakeVectorIndex:
    _indexes: dict[str, dict[str, _StoredVector]] = field(default_factory=dict)

    def create_index_if_missing(
        self, index_name: str, *, non_filterable_metadata_keys: list[str]
    ) -> None:
        self._indexes.setdefault(index_name, {})

    def put_vectors(
        self, index_name: str, vectors: list[tuple[str, list[float], dict[str, Any]]]
    ) -> None:
        table = self._indexes.setdefault(index_name, {})
        for key, values, metadata in vectors:
            table[key] = _StoredVector(key=key, values=list(values), metadata=dict(metadata))

    def query(
        self,
        index_name: str,
        query_vector: list[float],
        *,
        top_k: int,
        filter: dict[str, Any] | None = None,
    ) -> list[VectorMatch]:
        table = self._indexes.get(index_name, {})
        scored = [
            (stored, _cosine_distance(query_vector, stored.values))
            for stored in table.values()
            if _matches_filter(stored.metadata, filter)
        ]
        scored.sort(key=lambda pair: pair[1])
        return [
            VectorMatch(key=stored.key, metadata=stored.metadata, distance=distance)
            for stored, distance in scored[:top_k]
        ]

    def delete_vectors_if_present(self, index_name: str, keys: list[str]) -> None:
        table = self._indexes.get(index_name)
        if table is None:
            return
        for key in keys:
            table.pop(key, None)

    def delete_index_if_present(self, index_name: str) -> None:
        self._indexes.pop(index_name, None)
