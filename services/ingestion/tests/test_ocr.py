"""Textract retry policy and geometry conversion from fake responses
(docs/08-testing.md: "Textract geometry conversion from fake responses").
"""

from __future__ import annotations

from typing import Any

import pytest
from botocore.exceptions import ClientError

from common.ocr import NormalizedBoundingBox, TextLine, Textract
from ingestion.ocr import convert_lines_to_canonical, detect_lines_with_retry


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, "DetectDocumentText")


class _ScriptedClient:
    """A minimal stand-in for the boto3 `textract` client — deliberately dumb, per
    docs/08-testing.md's fakes philosophy, rather than a full captured-response player (that
    infrastructure belongs to a future phase that needs it for more than one test file)."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.call_count = 0

    def detect_document_text(self, Document: dict[str, Any]) -> dict[str, Any]:  # noqa: N803
        self.call_count += 1
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


_OK_RESPONSE = {
    "Blocks": [
        {
            "BlockType": "LINE",
            "Text": "Dear Ms. Alvarez,",
            "Geometry": {"BoundingBox": {"Left": 0.1, "Top": 0.2, "Width": 0.3, "Height": 0.05}},
        }
    ]
}


def test_detect_lines_with_retry_succeeds_after_transient_throttling() -> None:
    client = _ScriptedClient(
        [_client_error("ThrottlingException"), _client_error("ThrottlingException"), _OK_RESPONSE]
    )
    sleeps: list[float] = []

    lines = detect_lines_with_retry(Textract(client), b"fake-image-bytes", sleep=sleeps.append)

    assert [line.text for line in lines] == ["Dear Ms. Alvarez,"]
    assert client.call_count == 3
    assert len(sleeps) == 2
    assert sleeps[1] > sleeps[0]  # exponential backoff


def test_detect_lines_with_retry_gives_up_after_max_attempts() -> None:
    client = _ScriptedClient([_client_error("ThrottlingException")] * 4)

    with pytest.raises(ClientError):
        detect_lines_with_retry(
            Textract(client), b"fake-image-bytes", max_attempts=4, sleep=lambda _: None
        )
    assert client.call_count == 4


def test_detect_lines_with_retry_does_not_retry_non_throttling_errors() -> None:
    client = _ScriptedClient([_client_error("InvalidImageFormatException")])

    with pytest.raises(ClientError):
        detect_lines_with_retry(Textract(client), b"fake-image-bytes", sleep=lambda _: None)
    assert client.call_count == 1


def test_convert_lines_to_canonical_matches_hand_computed_example() -> None:
    lines = [
        TextLine(
            text="Dear Ms. Alvarez,",
            bbox=NormalizedBoundingBox(left=0.1, top=0.2, width=0.3, height=0.05),
        )
    ]

    converted = convert_lines_to_canonical(
        lines, embed_width_px=850, embed_height_px=1100, page_width_pt=612.0, page_height_pt=792.0
    )

    assert len(converted) == 1
    assert converted[0].text == "Dear Ms. Alvarez,"
    x0, y0, x1, y1 = converted[0].rect
    assert x0 == pytest.approx(0.1 * 612.0, abs=0.5)
    assert y0 == pytest.approx(0.2 * 792.0, abs=0.5)
    assert x1 == pytest.approx((0.1 + 0.3) * 612.0, abs=0.5)
    assert y1 == pytest.approx((0.2 + 0.05) * 792.0, abs=0.5)


def test_convert_lines_to_canonical_preserves_order_and_count() -> None:
    lines = [
        TextLine(text=f"line {i}", bbox=NormalizedBoundingBox(0.0, i * 0.1, 0.2, 0.05))
        for i in range(5)
    ]
    converted = convert_lines_to_canonical(
        lines, embed_width_px=850, embed_height_px=1100, page_width_pt=612.0, page_height_pt=792.0
    )
    assert [c.text for c in converted] == [f"line {i}" for i in range(5)]
