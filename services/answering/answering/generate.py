"""Sonnet 4.6 generation (docs/04-retrieval-and-citations.md #5, #9, "Streaming to the
client").

docs/10-roadmap.md's Phase 5 task list calls `generate.py` "non-streaming for now" — that
describes the client-facing contract (no partial output reaches the SPA yet, since there is no
realtime layer until Phase 6), not the Bedrock call itself. docs/04 is explicit that a
non-streaming *Bedrock* request risks an HTTP timeout at this `max_tokens`, so this module always
calls `BedrockMessages.stream()` and accumulates the whole response internally, into the same
shape a non-streaming response would have had — citation mapping doesn't need to know which path
produced it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from common.bedrock.messages import BedrockMessages
from common.config import Settings


@dataclass
class GeneratedMessage:
    content: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    first_token_ms: int | None = None


def generate(
    bedrock: BedrockMessages,
    settings: Settings,
    *,
    system: str,
    messages: list[dict[str, Any]],
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> GeneratedMessage:
    """Consumes the raw Bedrock event stream defensively (docs/04: "ignore unknown delta types,
    reconcile against the final message ... an unexpected shape degrades to 'citations appear at
    the end' rather than to a crash") — an unrecognised `type` or an `index` outside the blocks
    accumulated so far is simply skipped, never raised.

    `on_event`, when given, is called with every raw event before this function's own
    accumulation logic looks at it — `answering.stream.StreamPublisher.handle_event` is the one
    real caller (docs/04#streaming-to-the-client), republishing deltas/citations live while this
    function keeps building the same accumulated `GeneratedMessage` it always has, unchanged, for
    `answering.turn.run_turn`'s authoritative post-hoc citation mapping. If `on_event` raises
    (`answering.stream.TurnCancelledError`, on a `/cancel` request), the exception propagates out of
    this loop and the stream is abandoned mid-flight — the caller is expected to catch it.
    """
    start = time.monotonic()
    blocks: list[dict[str, Any]] = []
    result = GeneratedMessage(content=blocks)

    for event in bedrock.stream(
        model_id=settings.sonnet_model_id,
        system=system,
        messages=messages,
        max_tokens=settings.generation_max_tokens,
        thinking={"type": "adaptive"},
        effort=settings.generation_effort,
    ):
        if on_event is not None:
            on_event(event)

        event_type = event.get("type")

        if event_type == "message_start":
            usage = event.get("message", {}).get("usage", {})
            result.input_tokens = usage.get("input_tokens", 0)
            result.cache_read_input_tokens = usage.get("cache_read_input_tokens", 0)

        elif event_type == "content_block_start":
            block = dict(event.get("content_block") or {})
            block.setdefault("citations", [])
            blocks.append(block)

        elif event_type == "content_block_delta":
            index = event.get("index")
            if not isinstance(index, int) or index < 0 or index >= len(blocks):
                continue
            delta = event.get("delta", {})
            delta_type = delta.get("type")
            if delta_type == "text_delta":
                if result.first_token_ms is None:
                    result.first_token_ms = int((time.monotonic() - start) * 1000)
                blocks[index]["text"] = blocks[index].get("text", "") + delta.get("text", "")
            elif delta_type == "citations_delta":
                citation = delta.get("citation")
                if citation is not None:
                    blocks[index].setdefault("citations", []).append(citation)

        elif event_type == "message_delta":
            usage = event.get("usage", {})
            if "output_tokens" in usage:
                result.output_tokens = usage["output_tokens"]

        # content_block_stop / message_stop carry nothing this module needs.

    return result
