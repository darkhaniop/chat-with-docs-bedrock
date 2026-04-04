"""`FakeNova` — docs/08-testing.md: "returns deterministic pseudo-embeddings derived from a hash
of the input, with the configured dimension. Cosine similarity between two fake embeddings is
meaningless, so retrieval *ranking* is never unit-tested — only the plumbing." Structurally
satisfies `common.bedrock.embeddings.NovaEmbeddingsProtocol`.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Literal

from common.config import Settings


def _pseudo_vector(seed: bytes, dim: int) -> list[float]:
    values: list[float] = []
    counter = 0
    while len(values) < dim:
        digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        for offset in range(0, len(digest), 4):
            if len(values) >= dim:
                break
            as_int = int.from_bytes(digest[offset : offset + 4], "big")
            values.append((as_int / 0xFFFFFFFF) * 2 - 1)  # map to [-1, 1]
        counter += 1
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


@dataclass
class FakeNova:
    settings: Settings

    def embed_text(
        self, text: str, *, purpose: Literal["GENERIC_INDEX", "GENERIC_RETRIEVAL"]
    ) -> list[float]:
        return _pseudo_vector(text.encode(), self.settings.embed_dim)

    def embed_image(
        self,
        image_bytes: bytes,
        *,
        image_format: Literal["png", "jpeg"],
        purpose: Literal["GENERIC_INDEX", "GENERIC_RETRIEVAL"],
    ) -> list[float]:
        return _pseudo_vector(image_bytes, self.settings.embed_dim)
