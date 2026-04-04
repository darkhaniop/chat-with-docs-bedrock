"""`EmbedAndIndex`'s batching, retry, and key derivation (docs/08-testing.md's
`test_embed.py`)."""

from __future__ import annotations

from typing import Literal

import pytest
from botocore.exceptions import ClientError

from common.config import get_settings
from common.models import Chunk, Page, Sentence
from common.testing.embeddings import FakeNova
from ingestion.embed import (
    chunk_vector_key,
    embed_chunks,
    embed_image_with_retry,
    embed_pages,
    embed_text_with_retry,
    page_vector_key,
)


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, "InvokeModel")


class _ScriptedNova:
    """Deliberately dumb stand-in for `NovaEmbeddingsProtocol`, mirroring
    `test_ocr.py`'s `_ScriptedClient` — scripted responses/exceptions, one per call."""

    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.call_count = 0

    def _next(self) -> list[float]:
        self.call_count += 1
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        assert isinstance(response, list)
        return response

    def embed_text(
        self, text: str, *, purpose: Literal["GENERIC_INDEX", "GENERIC_RETRIEVAL"]
    ) -> list[float]:
        return self._next()

    def embed_image(
        self,
        image_bytes: bytes,
        *,
        image_format: Literal["png", "jpeg"],
        purpose: Literal["GENERIC_INDEX", "GENERIC_RETRIEVAL"],
    ) -> list[float]:
        return self._next()


def _chunk(*, document_id: str = "doc1", chunk_id: str = "chunk1", page_number: int = 1) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        document_id=document_id,
        project_id="proj1",
        page_number=page_number,
        ordinal=0,
        text="hello world",
        sentences=[Sentence(i=0, text="hello world", rects=[])],
        token_estimate=2,
        created_at="2026-01-01T00:00:00Z",
    )


def _page(*, document_id: str = "doc1", page_number: int = 1) -> Page:
    return Page(
        document_id=document_id,
        page_number=page_number,
        width=612.0,
        height=792.0,
        text_source="pdf",
        text_density=0.5,
    )


# -- key derivation -------------------------------------------------------------------------


def test_chunk_vector_key() -> None:
    assert chunk_vector_key("doc1", "chunk1") == "doc1:chunk1"


def test_page_vector_key_zero_pads() -> None:
    assert page_vector_key("doc1", 7) == "doc1:p0007"


# -- retry ------------------------------------------------------------------------------------


def test_embed_text_with_retry_succeeds_after_transient_throttling() -> None:
    nova = _ScriptedNova(
        [_client_error("ThrottlingException"), _client_error("ThrottlingException"), [1.0, 0.0]]
    )
    sleeps: list[float] = []

    vector = embed_text_with_retry(nova, "hello", sleep=sleeps.append)

    assert vector == [1.0, 0.0]
    assert nova.call_count == 3
    assert len(sleeps) == 2
    assert sleeps[1] > sleeps[0]


def test_embed_text_with_retry_gives_up_after_max_attempts() -> None:
    nova = _ScriptedNova([_client_error("ThrottlingException")] * 4)
    with pytest.raises(ClientError):
        embed_text_with_retry(nova, "hello", max_attempts=4, sleep=lambda _: None)
    assert nova.call_count == 4


def test_embed_text_with_retry_does_not_retry_non_throttling_errors() -> None:
    nova = _ScriptedNova([_client_error("ValidationException")])
    with pytest.raises(ClientError):
        embed_text_with_retry(nova, "hello", sleep=lambda _: None)
    assert nova.call_count == 1


def test_embed_image_with_retry_succeeds_after_transient_throttling() -> None:
    nova = _ScriptedNova([_client_error("ThrottlingException"), [0.0, 1.0]])
    vector = embed_image_with_retry(nova, b"fake-jpeg-bytes", sleep=lambda _: None)
    assert vector == [0.0, 1.0]
    assert nova.call_count == 2


# -- batching / key derivation via embed_chunks / embed_pages ---------------------------------


def test_embed_chunks_produces_one_vector_per_chunk_with_deterministic_keys() -> None:
    nova = FakeNova(get_settings())
    chunks = [
        _chunk(chunk_id="c1", page_number=1),
        _chunk(chunk_id="c2", page_number=2),
    ]
    vectors = embed_chunks(nova, chunks, max_concurrency=4)
    assert {key for key, _, _ in vectors} == {"doc1:c1", "doc1:c2"}
    for _key, values, metadata in vectors:
        assert len(values) == get_settings().embed_dim
        assert metadata["kind"] == "text"
        assert metadata["documentId"] == "doc1"
        assert metadata["chunkId"] in ("c1", "c2")


def test_embed_chunks_is_empty_for_no_chunks() -> None:
    nova = FakeNova(get_settings())
    assert embed_chunks(nova, [], max_concurrency=4) == []


def test_embed_pages_produces_one_vector_per_page_with_deterministic_keys() -> None:
    nova = FakeNova(get_settings())
    pages = [(_page(page_number=1), b"jpeg-bytes-1"), (_page(page_number=2), b"jpeg-bytes-2")]
    vectors = embed_pages(nova, pages, max_concurrency=4)
    assert {key for key, _, _ in vectors} == {"doc1:p0001", "doc1:p0002"}
    for _key, values, metadata in vectors:
        assert len(values) == get_settings().embed_dim
        assert metadata["kind"] == "page"
        assert metadata["documentId"] == "doc1"


def test_embed_pages_is_empty_for_no_pages() -> None:
    nova = FakeNova(get_settings())
    assert embed_pages(nova, [], max_concurrency=4) == []


def test_embed_chunks_propagates_a_non_throttling_error_from_the_pool() -> None:
    nova = _ScriptedNova([_client_error("ValidationException")])
    with pytest.raises(ClientError):
        embed_chunks(nova, [_chunk()], max_concurrency=1)
