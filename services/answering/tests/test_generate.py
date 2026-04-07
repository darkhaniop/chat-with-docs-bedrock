"""`answering.generate.generate` (docs/04-retrieval-and-citations.md #5, #9): accumulating
Bedrock's raw streaming events into content blocks, defensively — unknown event/delta types and
out-of-range indices are ignored, never fatal.
"""

from __future__ import annotations

from typing import Any

from answering.generate import generate
from common.config import get_settings


class _StubStream:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events
        self.calls: list[dict[str, Any]] = []

    def stream(self, **kwargs: Any):  # noqa: ANN201 — mirrors BedrockMessages.stream's Iterator
        self.calls.append(kwargs)
        yield from self._events


def test_accumulates_text_deltas_into_a_single_block() -> None:
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 100}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "lo."}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 12},
        },
        {"type": "message_stop"},
    ]
    bedrock = _StubStream(events)
    settings = get_settings()

    result = generate(bedrock, settings, system="sys", messages=[])

    assert result.content == [{"type": "text", "text": "Hello.", "citations": []}]
    assert result.input_tokens == 100
    assert result.output_tokens == 12
    assert result.first_token_ms is not None
    [call] = bedrock.calls
    assert call["model_id"] == settings.sonnet_model_id
    assert call["thinking"] == {"type": "adaptive"}


def test_citations_delta_attaches_to_the_current_block() -> None:
    citation_payload = {
        "type": "content_block_location",
        "cited_text": "94% uptime",
        "document_index": 0,
        "document_title": "report.pdf — page 7",
        "start_block_index": 0,
        "end_block_index": 1,
    }
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "94% uptime."},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "citations_delta", "citation": citation_payload},
        },
        {"type": "content_block_stop", "index": 0},
    ]
    bedrock = _StubStream(events)

    result = generate(bedrock, get_settings(), system="sys", messages=[])

    assert result.content[0]["citations"] == [citation_payload]


def test_ignores_unknown_delta_types_and_out_of_range_indices() -> None:
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
        {"type": "some_future_event_type", "index": 0},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "some_future_delta_type"}},
        {"type": "content_block_delta", "index": 99, "delta": {"type": "text_delta", "text": "x"}},
    ]
    bedrock = _StubStream(events)

    result = generate(bedrock, get_settings(), system="sys", messages=[])

    assert result.content == [{"type": "thinking", "citations": []}]
    assert result.first_token_ms is None


def test_first_token_ms_is_only_set_on_the_first_text_delta() -> None:
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "a"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "b"}},
    ]
    bedrock = _StubStream(events)

    result = generate(bedrock, get_settings(), system="sys", messages=[])

    assert result.content[0]["text"] == "ab"
    assert isinstance(result.first_token_ms, int)
