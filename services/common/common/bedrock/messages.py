"""Thin wrapper over `bedrock-runtime` InvokeModel for the Anthropic Messages schema.

ADR-001: everything goes through Bedrock, not a first-party Anthropic client. Citations work on
Bedrock via `InvokeModel`/`InvokeModelWithResponseStream` with the native Anthropic request body
(`anthropic_version`), not via the Converse API, which has no `citations` parameter.

Retry/backoff on throttling belongs to the caller (Phase 5/6 answering Lambda); this module is
the interface skeleton the rest of the system codes against, confirmed against real Bedrock by
the Phase 0 contract smoke test (`tests/contract/smoke_citations.py`).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from common.config import Settings


@dataclass(frozen=True)
class DocumentBlock:
    """One chunk, serialised as a Citations-API custom-content document (docs/04 #5)."""

    title: str
    context: str
    sentences: list[str]


@dataclass(frozen=True)
class Citation:
    """A `content_block_location`.

    `end_block_index` is exclusive — confirmed live: a two-sentence document, asked a question
    answered by the second sentence, returned `start_block_index=1, end_block_index=2`.
    """

    cited_text: str
    document_index: int
    document_title: str
    start_block_index: int
    end_block_index: int


def document_block(block: DocumentBlock) -> dict[str, Any]:
    """Build a Citations-API custom-content `document` block, one text block per sentence.

    The sentence list must be exactly `chunk.sentences[*].text`, in order — this correspondence
    is the citation map (docs/04 #5, ADR-005). Nothing may reorder, filter, or merge sentences
    between DynamoDB and this call.
    """
    return {
        "type": "document",
        "source": {
            "type": "content",
            "content": [{"type": "text", "text": sentence} for sentence in block.sentences],
        },
        "title": block.title,
        "context": block.context,
        "citations": {"enabled": True},
    }


class BedrockMessages:
    def __init__(self, settings: Settings, client: Any) -> None:
        self._settings = settings
        self._client = client

    def _body(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        thinking: dict[str, Any] | None,
        effort: str | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if thinking is not None:
            body["thinking"] = thinking
        if effort is not None:
            body["output_config"] = {"effort": effort}
        return body

    def create(
        self,
        *,
        model_id: str,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        thinking: dict[str, Any] | None = None,
        effort: str | None = None,
    ) -> dict[str, Any]:
        """Non-streaming call — used for the Haiku rewrite. Sonnet generation always streams."""
        body = self._body(
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            thinking=thinking,
            effort=effort,
        )
        response = self._client.invoke_model(modelId=model_id, body=json.dumps(body))
        result: dict[str, Any] = json.loads(response["body"].read())
        return result

    def stream(
        self,
        *,
        model_id: str,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        thinking: dict[str, Any] | None = None,
        effort: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Streaming call. Yields decoded event dicts in Bedrock's Anthropic event-stream shape.

        Confirmed live event sequence for a request with `citations.enabled=True`: message_start
        -> content_block_start -> content_block_delta (`citations_delta` and `text_delta`,
        interleaved, `citations_delta` may arrive before the text it anchors to) ->
        content_block_stop -> message_delta -> message_stop.
        """
        body = self._body(
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            thinking=thinking,
            effort=effort,
        )
        response = self._client.invoke_model_with_response_stream(
            modelId=model_id, body=json.dumps(body)
        )
        for event in response["body"]:
            chunk = event.get("chunk")
            if chunk is not None:
                yield json.loads(chunk["bytes"])
