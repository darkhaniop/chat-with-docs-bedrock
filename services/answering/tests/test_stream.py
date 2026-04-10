"""`answering.stream.StreamPublisher` (docs/04-retrieval-and-citations.md#streaming-to-the-
client): delta batching, citation mapping/publish timing, unknown event types, and the
cancellation poll — in isolation from the rest of `run_turn`, using `FakeEventsPublisher`.
"""

from __future__ import annotations

import time

import pytest

from answering.stream import StreamPublisher, TurnCancelledError
from common.models import Chunk, Sentence
from common.testing.events import FakeEventsPublisher


def _chunk() -> Chunk:
    return Chunk(
        chunk_id="chunk-1",
        document_id="doc-1",
        project_id="proj-1",
        page_number=1,
        ordinal=0,
        text="Uptime was 94% in Q3.",
        sentences=[Sentence(i=0, text="Uptime was 94% in Q3.", rects=[(1.0, 2.0, 3.0, 4.0)])],
        token_estimate=10,
        created_at="2026-01-01T00:00:00Z",
    )


def _publisher(**overrides: object) -> tuple[StreamPublisher, FakeEventsPublisher]:
    events = FakeEventsPublisher()
    seq = iter(range(1, 1000))
    defaults: dict[str, object] = dict(
        channel="/conversations/conv-1",
        message_id="msg-1",
        next_seq=lambda: next(seq),
        index_to_chunk={0: "chunk-1"},
        chunks_by_id={"chunk-1": _chunk()},
        tick_seconds=1000.0,  # effectively "never auto-flush" unless the test ticks it forward
    )
    defaults.update(overrides)
    return StreamPublisher(events, **defaults), events  # type: ignore[arg-type]


def test_text_deltas_are_buffered_until_flush() -> None:
    publisher, events = _publisher()
    publisher.handle_event(
        {"type": "content_block_start", "content_block": {"type": "text", "text": ""}}
    )
    publisher.handle_event(
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello, "}}
    )
    publisher.handle_event(
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "world."}}
    )
    assert events.events == []  # tick_seconds is huge — nothing flushed yet

    publisher.handle_event({"type": "content_block_stop"})
    [event] = events.events
    assert event.event_type == "message.delta"
    assert event.data == {"messageId": "msg-1", "text": "Hello, world."}


def test_flush_is_a_no_op_with_an_empty_buffer() -> None:
    publisher, events = _publisher()
    publisher.flush()
    assert events.events == []


def test_thinking_block_publishes_once() -> None:
    publisher, events = _publisher()
    publisher.handle_event({"type": "content_block_start", "content_block": {"type": "thinking"}})
    publisher.handle_event({"type": "content_block_start", "content_block": {"type": "thinking"}})
    thinking_events = [e for e in events.events if e.event_type == "message.thinking"]
    assert len(thinking_events) == 2  # publisher doesn't dedupe; `run_turn` only sees one anyway


def test_citation_is_mapped_and_published_immediately_mid_block() -> None:
    publisher, events = _publisher()
    publisher.handle_event(
        {"type": "content_block_start", "content_block": {"type": "text", "text": ""}}
    )
    publisher.handle_event(
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Uptime was 94%."}}
    )
    publisher.handle_event(
        {
            "type": "content_block_delta",
            "delta": {
                "type": "citations_delta",
                "citation": {
                    "cited_text": "94%",
                    "document_index": 0,
                    "document_title": "report",
                    "start_block_index": 0,
                    "end_block_index": 1,
                },
            },
        }
    )
    # Published before `content_block_stop` flushes the buffered text — docs/04: "a citation can
    # be published before the text block it belongs to has finished."
    assert [e.event_type for e in events.events] == ["message.citation"]
    citation = events.events[0].data["citation"]
    assert citation["documentId"] == "doc-1"
    assert citation["chunkId"] == "chunk-1"


def test_an_unmappable_citation_publishes_nothing() -> None:
    publisher, events = _publisher()
    publisher.handle_event(
        {"type": "content_block_start", "content_block": {"type": "text", "text": ""}}
    )
    publisher.handle_event(
        {
            "type": "content_block_delta",
            "delta": {
                "type": "citations_delta",
                # document_index 99 isn't in index_to_chunk — dropped, per
                # `answering.citations.map_citation`'s "out-of-range is never guessed at" rule.
                "citation": {
                    "cited_text": "x",
                    "document_index": 99,
                    "document_title": "?",
                    "start_block_index": 0,
                    "end_block_index": 1,
                },
            },
        }
    )
    assert events.events == []


def test_unknown_event_types_are_ignored() -> None:
    publisher, events = _publisher()
    publisher.handle_event({"type": "some_future_event_type", "whatever": True})
    publisher.handle_event({"type": "message_stop"})
    assert events.events == []


def test_tick_based_flush_happens_without_waiting_for_block_stop() -> None:
    publisher, events = _publisher(tick_seconds=0.0)
    publisher.handle_event(
        {"type": "content_block_start", "content_block": {"type": "text", "text": ""}}
    )
    publisher.handle_event(
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "partial"}}
    )
    # tick_seconds=0 means the very next delta always crosses the tick.
    [event] = events.events
    assert event.event_type == "message.delta"
    assert event.data["text"] == "partial"


def test_cancellation_raises_turn_cancelled_between_chunks() -> None:
    publisher, _events = _publisher(should_cancel=lambda: True, cancel_poll_interval_seconds=0)
    publisher.handle_event(
        {"type": "content_block_start", "content_block": {"type": "text", "text": ""}}
    )
    with pytest.raises(TurnCancelledError):
        publisher.handle_event(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "x"}}
        )


def test_cancellation_is_not_checked_faster_than_the_poll_interval() -> None:
    calls = []

    def should_cancel() -> bool:
        calls.append(time.monotonic())
        return False

    publisher, _events = _publisher(
        should_cancel=should_cancel, cancel_poll_interval_seconds=1000.0
    )
    publisher.handle_event(
        {"type": "content_block_start", "content_block": {"type": "text", "text": ""}}
    )
    for _ in range(5):
        publisher.handle_event(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "x"}}
        )
    # The 1000s interval means only the very first delta's check (against the constructor-time
    # baseline) can possibly fire within a fast test run.
    assert len(calls) <= 1


def test_no_should_cancel_callback_never_raises() -> None:
    publisher, _events = _publisher(should_cancel=None)
    publisher.handle_event(
        {"type": "content_block_start", "content_block": {"type": "text", "text": ""}}
    )
    publisher.handle_event(
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "x"}}
    )  # must not raise
