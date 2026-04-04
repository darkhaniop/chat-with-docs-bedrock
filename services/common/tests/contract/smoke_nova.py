"""Pin the Nova Multimodal Embeddings request/response contract.

- Request: `{"taskType": "SINGLE_EMBEDDING", "singleEmbeddingParams": {...}}`. The naive first
  attempt (dimension + text only) fails: `embeddingPurpose` is required.
- `embeddingDimension` is a fixed enum: 384, 1024, and 3072 all succeeded and produced a vector
  of exactly that length; 10000 was rejected as "not a valid enum value". Dimension is
  configurable, not fixed to one value.
- `embeddingPurpose` is a fixed enum: `GENERIC_INDEX` and `GENERIC_RETRIEVAL` succeeded;
  `DOCUMENT` and `QUERY` were rejected.
- Response: `{"embeddings": [{"embeddingType": "TEXT" | "IMAGE", "embedding": [float, ...]}]}`.
- Image mode takes `{"format": "png", "source": {"bytes": "<base64>"}}` and returns
  `embeddingType: "IMAGE"` in the same vector space (same dimension, comparable by cosine).
"""

from __future__ import annotations

import base64
import json

import pytest

from common.bedrock.embeddings import NovaEmbeddings
from common.config import Settings

pytestmark = pytest.mark.contract

# A 1x1 red pixel PNG, valid enough for the API to accept and embed.
_PIXEL_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_embed_text_returns_configured_dimension(
    settings: Settings, bedrock_runtime: object
) -> None:
    embeddings = NovaEmbeddings(settings, bedrock_runtime)
    vector = embeddings.embed_text("hello world", purpose="GENERIC_INDEX")
    assert len(vector) == settings.embed_dim
    assert any(v != 0 for v in vector)


def test_embed_image_same_dimension_as_text(settings: Settings, bedrock_runtime: object) -> None:
    embeddings = NovaEmbeddings(settings, bedrock_runtime)
    image_bytes = base64.b64decode(_PIXEL_PNG_B64)
    vector = embeddings.embed_image(image_bytes, image_format="png", purpose="GENERIC_INDEX")
    assert len(vector) == settings.embed_dim


def test_dimension_is_a_fixed_enum(settings: Settings, bedrock_runtime: object) -> None:
    for dim in (384, 1024, 3072):
        scoped_settings = settings.model_copy(update={"embed_dim": dim})
        embeddings = NovaEmbeddings(scoped_settings, bedrock_runtime)
        vector = embeddings.embed_text("hello world", purpose="GENERIC_RETRIEVAL")
        assert len(vector) == dim


def _invoke_raw(bedrock_runtime: object, body: dict[str, object]) -> str:
    try:
        bedrock_runtime.invoke_model(  # type: ignore[attr-defined]
            modelId="amazon.nova-2-multimodal-embeddings-v1:0", body=json.dumps(body)
        )
    except Exception as exc:  # noqa: BLE001 — we only want the message text to compare
        return str(exc)
    raise AssertionError("expected a ValidationException, the call unexpectedly succeeded")


def test_batch_embedding_task_type_does_not_exist(
    settings: Settings, bedrock_runtime: object
) -> None:
    bogus = _invoke_raw(bedrock_runtime, {"taskType": "NOT_A_REAL_TASK_TYPE"})
    batch = _invoke_raw(
        bedrock_runtime,
        {
            "taskType": "BATCH_EMBEDDING",
            "batchEmbeddingParams": {
                "embeddingDimension": settings.embed_dim,
                "embeddingPurpose": "GENERIC_INDEX",
                "texts": [{"truncationMode": "END", "value": "hello"}],
            },
        },
    )
    assert bogus == batch
    assert "required key [messages]" in bogus


def test_a_malformed_single_embedding_request_errors_on_the_actual_field(
    bedrock_runtime: object,
) -> None:
    error = _invoke_raw(
        bedrock_runtime,
        {
            "taskType": "SINGLE_EMBEDDING",
            "singleEmbeddingParams": {
                "embeddingDimension": 1024,
                "embeddingPurpose": "GENERIC_INDEX",
                # should be an object, not a list:
                "text": [{"truncationMode": "END", "value": "hi"}],
            },
        },
    )
    assert "singleEmbeddingParams/text" in error
