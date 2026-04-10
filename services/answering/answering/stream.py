"""Republishing the Bedrock stream to AppSync Events
(docs/04-retrieval-and-citations.md#streaming-to-the-client) and enforcing `/cancel` between
chunks (docs/05-api-contracts.md).

`StreamPublisher` is the stateful piece `answering.generate.generate`'s `on_event` hook drives.
It does not change what the *persisted* message ends up being — `answering.turn.run_turn` still
builds the final `citations`/`text` from `GeneratedMessage.content` exactly as Phase 5 did, from
scratch, after `generate()` returns. This module only produces a live preview:

- `message.delta` text is batched on a `stream_batch_tick_ms` timer so a fast generation doesn't
  produce hundreds of tiny publishes.
- `message.citation` is mapped and published as soon as its `citations_delta` arrives, using the
  citation's block-start offset (already known — it's the running total of every *closed*
  block's length) and the block's *partial* length so far as a best-effort, self-correcting
  `spanEnd` estimate. docs/06-frontend.md's reducer only needs `spanStart` to decide when to
  attach a citation inline (`spanStart <= text.length`); `message.completed` -> `GET
  .../messages` is what replaces this whole preview with the authoritative record, `spanEnd`
  included.
- Cancellation (`Message.cancelRequested`) is polled at a coarser interval than the tick — a
  DynamoDB `GetItem` on every text delta would turn a chatty stream into a hot loop for a flag
  that only a human clicking "stop" ever sets.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from answering.citations import map_citation
from common.bedrock.messages import Citation
from common.events import EventsPublisher
from common.models import Chunk

# docs/05-api-contracts.md#appsync-events: the conversation-channel event vocabulary. No
# `message.cancelled` exists — cancellation surfaces as `message.failed` with this `code`,
# matching `Message.status = "CANCELLED"` on the persisted record (docs/02-data-model.md).
CANCELLED_CODE = "CANCELLED"

_CANCEL_POLL_INTERVAL_SECONDS = 1.0


class TurnCancelledError(Exception):
    """Raised out of `generate()`'s event loop when `should_cancel()` reports the user asked to
    stop. Caught by `answering.turn.run_turn`, which persists `status: "CANCELLED"` — a distinct
    branch from the blanket failure catch, so a cancellation is never miscounted as a bug."""


class StreamPublisher:
    def __init__(
        self,
        events: EventsPublisher,
        *,
        channel: str,
        message_id: str,
        next_seq: Callable[[], int],
        index_to_chunk: dict[int, str],
        chunks_by_id: dict[str, Chunk],
        tick_seconds: float,
        should_cancel: Callable[[], bool] | None = None,
        cancel_poll_interval_seconds: float = _CANCEL_POLL_INTERVAL_SECONDS,
    ) -> None:
        self._events = events
        self._channel = channel
        self._message_id = message_id
        self._next_seq = next_seq
        self._index_to_chunk = index_to_chunk
        self._chunks_by_id = chunks_by_id
        self._tick_seconds = tick_seconds
        self._should_cancel = should_cancel
        self._cancel_poll_interval_seconds = cancel_poll_interval_seconds

        self._buffer = ""
        self._last_flush = time.monotonic()
        self._last_cancel_check = time.monotonic()
        self._running_offset = 0
        self._citation_count = 0
        self._block_start: int | None = None
        self._block_len = 0

    def _publish(self, event_type: str, data: dict[str, Any]) -> None:
        self._events.publish(self._channel, event_type, data, seq=self._next_seq())

    def flush(self) -> None:
        if self._buffer:
            self._publish("message.delta", {"messageId": self._message_id, "text": self._buffer})
            self._buffer = ""
        self._last_flush = time.monotonic()

    def _maybe_check_cancelled(self) -> None:
        if self._should_cancel is None:
            return
        now = time.monotonic()
        if now - self._last_cancel_check < self._cancel_poll_interval_seconds:
            return
        self._last_cancel_check = now
        if self._should_cancel():
            raise TurnCancelledError()

    def handle_event(self, event: dict[str, Any]) -> None:
        """Passed as `generate()`'s `on_event` — called once per raw Bedrock stream event,
        alongside (not instead of) `generate()`'s own accumulation."""
        event_type = event.get("type")

        if event_type == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "thinking":
                self._publish("message.thinking", {"messageId": self._message_id})
            elif block.get("type") == "text":
                self._block_start = self._running_offset
                self._block_len = 0

        elif event_type == "content_block_delta":
            delta = event.get("delta") or {}
            delta_type = delta.get("type")

            if delta_type == "text_delta" and self._block_start is not None:
                text = str(delta.get("text", ""))
                self._block_len += len(text)
                self._buffer += text
                self._maybe_check_cancelled()
                if time.monotonic() - self._last_flush >= self._tick_seconds:
                    self.flush()

            elif delta_type == "citations_delta" and self._block_start is not None:
                raw = delta.get("citation")
                if raw is not None:
                    self._publish_citation(raw)

        elif event_type == "content_block_stop" and self._block_start is not None:
            self._running_offset += self._block_len
            self.flush()
            self._block_start = None
            self._block_len = 0

    def _publish_citation(self, raw: dict[str, Any]) -> None:
        assert self._block_start is not None  # narrowed by caller
        citation = Citation(
            cited_text=raw.get("cited_text", ""),
            document_index=raw.get("document_index", -1),
            document_title=raw.get("document_title", ""),
            start_block_index=raw.get("start_block_index", 0),
            end_block_index=raw.get("end_block_index", 0),
        )
        mapped = map_citation(
            citation,
            citation_id=f"c{self._citation_count}",
            index_to_chunk=self._index_to_chunk,
            chunks_by_id=self._chunks_by_id,
            span_start=self._block_start,
            span_end=self._block_start + self._block_len,
        )
        if mapped is None:
            return
        self._citation_count += 1
        self._publish(
            "message.citation",
            {"messageId": self._message_id, "citation": mapped.model_dump(by_alias=True)},
        )
