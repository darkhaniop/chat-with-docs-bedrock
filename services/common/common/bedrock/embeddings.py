"""Nova Multimodal Embeddings adapter — text and image modes into one vector space (ADR-002).

Request/response shape confirmed live (`tests/contract/smoke_nova.py`):

    {"taskType": "SINGLE_EMBEDDING",
     "singleEmbeddingParams": {
         "embeddingDimension": 1024,             # enum: 384 | 1024 | 3072
         "embeddingPurpose": "GENERIC_INDEX",    # enum: GENERIC_INDEX | GENERIC_RETRIEVAL
         "text": {"truncationMode": "END", "value": "..."}}}
                                                  # or "image": {"format": "png",
                                                  #              "source": {"bytes": "<base64>"}}

    -> {"embeddings": [{"embeddingType": "TEXT" | "IMAGE", "embedding": [float, ...]}]}

Batch limits are not pinned here — the smoke test only established the single-item shape;
Phase 4 pins the batch shape once it batches embedding calls at ingest time.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Literal, Protocol

from common.config import Settings


class NovaEmbeddingsProtocol(Protocol):
    """Mirrors `common.events.EventsPublisher`'s pattern: the subset of `NovaEmbeddings` ingest/
    answering code depends on, so `common.testing.embeddings.FakeNova` can structurally satisfy
    it without inheriting from the real (boto3-backed) class."""

    def embed_text(
        self, text: str, *, purpose: Literal["GENERIC_INDEX", "GENERIC_RETRIEVAL"]
    ) -> list[float]: ...

    def embed_image(
        self,
        image_bytes: bytes,
        *,
        image_format: Literal["png", "jpeg"],
        purpose: Literal["GENERIC_INDEX", "GENERIC_RETRIEVAL"],
    ) -> list[float]: ...


class NovaEmbeddings:
    def __init__(self, settings: Settings, client: Any) -> None:
        self._settings = settings
        self._client = client

    def _invoke(self, params: dict[str, Any]) -> list[float]:
        body = {"taskType": "SINGLE_EMBEDDING", "singleEmbeddingParams": params}
        response = self._client.invoke_model(
            modelId=self._settings.nova_model_id, body=json.dumps(body)
        )
        payload = json.loads(response["body"].read())
        embedding: list[float] = payload["embeddings"][0]["embedding"]
        return embedding

    def embed_text(
        self, text: str, *, purpose: Literal["GENERIC_INDEX", "GENERIC_RETRIEVAL"]
    ) -> list[float]:
        return self._invoke(
            {
                "embeddingDimension": self._settings.embed_dim,
                "embeddingPurpose": purpose,
                "text": {"truncationMode": "END", "value": text},
            }
        )

    def embed_image(
        self,
        image_bytes: bytes,
        *,
        image_format: Literal["png", "jpeg"],
        purpose: Literal["GENERIC_INDEX", "GENERIC_RETRIEVAL"],
    ) -> list[float]:
        return self._invoke(
            {
                "embeddingDimension": self._settings.embed_dim,
                "embeddingPurpose": purpose,
                "image": {
                    "format": image_format,
                    "source": {"bytes": base64.b64encode(image_bytes).decode("ascii")},
                },
            }
        )
