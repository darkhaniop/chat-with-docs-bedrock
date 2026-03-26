"""Textract adapter — synchronous `DetectDocumentText` per page render.

Confirmed live against a generated fixture image: `LINE` blocks carry `Geometry.BoundingBox`
as `{Left, Top, Width, Height}` normalised to `[0, 1]` of the image sent, origin top-left —
matching docs/02-data-model.md#coordinate-systems. `WORD` blocks carry the same shape plus a
`Polygon`; they are stored for a future word-level highlight mode but unused for now.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NormalizedBoundingBox:
    """Textract's `Geometry.BoundingBox`: fractions of the source image, origin top-left."""

    left: float
    top: float
    width: float
    height: float


@dataclass(frozen=True)
class TextLine:
    text: str
    bbox: NormalizedBoundingBox


class Textract:
    def __init__(self, client: Any) -> None:
        self._client = client

    def detect_lines(self, image_bytes: bytes) -> list[TextLine]:
        response = self._client.detect_document_text(Document={"Bytes": image_bytes})
        lines = []
        for block in response["Blocks"]:
            if block["BlockType"] != "LINE":
                continue
            box = block["Geometry"]["BoundingBox"]
            lines.append(
                TextLine(
                    text=block["Text"],
                    bbox=NormalizedBoundingBox(
                        left=box["Left"], top=box["Top"], width=box["Width"], height=box["Height"]
                    ),
                )
            )
        return lines
