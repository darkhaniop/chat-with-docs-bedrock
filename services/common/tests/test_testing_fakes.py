"""`FakeVectorIndex`/`FakeNova` themselves (docs/08-testing.md's "dumb recorded-response
player" fakes) — exercised directly here so a bug in the fake doesn't silently invalidate every
test in `services/answering/tests/test_fusion.py` that depends on it."""

from __future__ import annotations

from common.config import get_settings
from common.testing.embeddings import FakeNova
from common.testing.vectors import FakeVectorIndex


def test_put_then_query_returns_exact_match_first() -> None:
    index = FakeVectorIndex()
    index.create_index_if_missing("idx", non_filterable_metadata_keys=["preview"])
    index.put_vectors(
        "idx",
        [
            ("a", [1.0, 0.0, 0.0], {"documentId": "doc1"}),
            ("b", [0.0, 1.0, 0.0], {"documentId": "doc2"}),
        ],
    )
    results = index.query("idx", [1.0, 0.0, 0.0], top_k=2)
    assert [r.key for r in results] == ["a", "b"]
    assert results[0].distance == 0.0


def test_query_respects_equality_filter() -> None:
    index = FakeVectorIndex()
    index.create_index_if_missing("idx", non_filterable_metadata_keys=[])
    index.put_vectors(
        "idx",
        [
            ("a", [1.0, 0.0], {"documentId": "doc1"}),
            ("b", [1.0, 0.0], {"documentId": "doc2"}),
        ],
    )
    results = index.query("idx", [1.0, 0.0], top_k=10, filter={"documentId": "doc2"})
    assert [r.key for r in results] == ["b"]


def test_query_respects_in_filter() -> None:
    index = FakeVectorIndex()
    index.create_index_if_missing("idx", non_filterable_metadata_keys=[])
    index.put_vectors(
        "idx",
        [
            ("a", [1.0, 0.0], {"documentId": "doc1"}),
            ("b", [1.0, 0.0], {"documentId": "doc2"}),
            ("c", [1.0, 0.0], {"documentId": "doc3"}),
        ],
    )
    results = index.query(
        "idx", [1.0, 0.0], top_k=10, filter={"documentId": {"$in": ["doc1", "doc3"]}}
    )
    assert {r.key for r in results} == {"a", "c"}


def test_delete_vectors_if_present_removes_keys() -> None:
    index = FakeVectorIndex()
    index.create_index_if_missing("idx", non_filterable_metadata_keys=[])
    index.put_vectors("idx", [("a", [1.0, 0.0], {})])
    index.delete_vectors_if_present("idx", ["a"])
    assert index.query("idx", [1.0, 0.0], top_k=10) == []


def test_delete_vectors_if_present_on_missing_index_is_a_no_op() -> None:
    index = FakeVectorIndex()
    index.delete_vectors_if_present("does-not-exist", ["a"])  # must not raise


def test_delete_index_if_present_on_missing_index_is_a_no_op() -> None:
    index = FakeVectorIndex()
    index.delete_index_if_present("does-not-exist")  # must not raise


def test_fake_nova_is_deterministic_and_configured_dimension() -> None:
    nova = FakeNova(get_settings())
    v1 = nova.embed_text("hello world", purpose="GENERIC_INDEX")
    v2 = nova.embed_text("hello world", purpose="GENERIC_RETRIEVAL")
    assert v1 == v2
    assert len(v1) == get_settings().embed_dim


def test_fake_nova_different_inputs_produce_different_vectors() -> None:
    nova = FakeNova(get_settings())
    assert nova.embed_text("a", purpose="GENERIC_INDEX") != nova.embed_text(
        "b", purpose="GENERIC_INDEX"
    )


def test_fake_nova_embed_image_is_deterministic() -> None:
    nova = FakeNova(get_settings())
    v1 = nova.embed_image(b"\x00\x01\x02", image_format="jpeg", purpose="GENERIC_INDEX")
    v2 = nova.embed_image(b"\x00\x01\x02", image_format="jpeg", purpose="GENERIC_INDEX")
    assert v1 == v2
    assert len(v1) == get_settings().embed_dim
