"""Pin the Bedrock Citations API contract.

Two custom-content documents of two sentences each; a question answered by the second sentence
of the first document.

- The response's `citations` entries are `{"type": "content_block_location", "cited_text",
  "document_index", "document_title", "start_block_index", "end_block_index"}`.
- `end_block_index` is **exclusive**: asking about the second sentence (index 1) of a
  two-sentence document returned `start_block_index=1, end_block_index=2`, i.e.
  `sentences[1:2]`.
- The streamed citation delta's type name is `citations_delta`, nested under
  `content_block_delta`, carrying the same `content_block_location` payload. It can arrive
  before the `text_delta` events for the block it anchors to.
"""

from __future__ import annotations

import pytest

from common.bedrock.messages import BedrockMessages, DocumentBlock, document_block
from common.config import Settings

pytestmark = pytest.mark.contract

DOC_ONE = DocumentBlock(
    title="doc-one",
    context="documentId=fixture-1",
    sentences=["The sky is blue.", "The grass is green."],
)
DOC_TWO = DocumentBlock(
    title="doc-two",
    context="documentId=fixture-2",
    sentences=["Water boils at 100 degrees Celsius.", "Ice melts at 0 degrees Celsius."],
)
QUESTION = "What color is the grass?"


def _messages() -> list[dict[str, object]]:
    return [
        {
            "role": "user",
            "content": [
                document_block(DOC_ONE),
                document_block(DOC_TWO),
                {"type": "text", "text": QUESTION},
            ],
        }
    ]


def test_content_block_location_shape_and_end_index_exclusive(
    settings: Settings, bedrock_runtime: object
) -> None:
    client = BedrockMessages(settings, bedrock_runtime)
    response = client.create(
        model_id=settings.sonnet_model_id,
        system="Answer strictly from the provided documents and cite your claims.",
        messages=_messages(),
        max_tokens=500,
    )
    citations = [c for block in response["content"] for c in block.get("citations") or []]
    assert citations, "expected at least one citation"
    citation = citations[0]
    assert citation["type"] == "content_block_location"
    assert citation["document_index"] == 0
    # "The grass is green." is sentence index 1 of DOC_ONE. end_block_index exclusive means the
    # range [start, end) covers exactly that one sentence.
    assert citation["start_block_index"] == 1
    assert citation["end_block_index"] == 2
    sentences = DOC_ONE.sentences[citation["start_block_index"] : citation["end_block_index"]]
    assert sentences == ["The grass is green."]


def test_streamed_citation_delta_event_name(settings: Settings, bedrock_runtime: object) -> None:
    client = BedrockMessages(settings, bedrock_runtime)
    events = list(
        client.stream(
            model_id=settings.sonnet_model_id,
            system="Answer strictly from the provided documents and cite your claims.",
            messages=_messages(),
            max_tokens=500,
        )
    )
    citation_deltas = [
        e
        for e in events
        if e.get("type") == "content_block_delta" and e["delta"].get("type") == "citations_delta"
    ]
    assert citation_deltas, "expected a citations_delta event in the stream"
    assert citation_deltas[0]["delta"]["citation"]["type"] == "content_block_location"
