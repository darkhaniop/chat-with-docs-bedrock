"""The full answer turn (docs/04-retrieval-and-citations.md "The answer turn, end to end"):
rewrite -> embed/search/fuse/select -> guard input -> prompt -> generate -> map citations ->
guard output -> persist.

Phase 6: `handler.py` invokes this off the SQS answer queue (docs/01-architecture.md#answering-
sqs--answering-lambda), not synchronously from `api` anymore, and `generate()` republishes to
AppSync Events as it streams (`answering.stream.StreamPublisher`) instead of only being
accumulated silently — but the rewrite/retrieve/prompt/citation logic below is unchanged from
Phase 5, exactly as that phase's own docstring said it would be.

Always returns a persisted `Message` — never raises for an "expected" failure (guardrail block,
mapping issue, cancellation). A failure after the conversation lock was claimed still needs the
lock released and *something* persisted for the client to eventually see (via the channel or a
`GET`), so every such failure mode ends in a terminal assistant message rather than an
exception. Only a genuinely unexpected error *before* a message can be constructed — none
observed in practice — would propagate to `handler.py`, where it's expected to reach SQS's
redrive/DLQ path (docs/04-retrieval-and-citations.md#failure-behaviour).
"""

from __future__ import annotations

import time
from typing import Literal

from aws_lambda_powertools import Logger

from answering.citations import map_citation
from answering.generate import generate
from answering.prompt import SYSTEM_PROMPT, build_prompt_content, thin_pages
from answering.retrieve import retrieve
from answering.rewrite import HistoryTurn, rewrite_query
from answering.stream import CANCELLED_CODE, StreamPublisher, TurnCancelledError
from common.bedrock.embeddings import NovaEmbeddingsProtocol
from common.bedrock.guardrail import Guardrail
from common.bedrock.messages import BedrockMessages, Citation
from common.config import Settings
from common.events import EventsPublisher
from common.models import CitationRecord, LatencyMs, Message, RetrievedRef, Usage
from common.repo import Repo
from common.storage import DocumentsStore
from common.vectors import VectorIndexProtocol

logger = Logger(service="answering", child=True)

_BLOCKED_TEXT = "This message was blocked by content safety guardrails."
_FAILED_TEXT = "Something went wrong while generating this answer. Please try again."
_CANCELLED_TEXT = "Cancelled."


def _guardrail_blocked(
    guardrail: Guardrail, text: str, *, source: Literal["INPUT", "OUTPUT"]
) -> bool:
    """`Guardrail.apply` itself does not catch exceptions (docs/07-security.md: "Guardrail
    failures fail open ... callers should catch adapter exceptions and proceed, not block the
    turn") — this is that catch, in the one place both call sites share."""
    try:
        return guardrail.apply(text, source=source).blocked
    except Exception:
        logger.warning("guardrail check failed; failing open", source=source)
        return False


def run_turn(
    *,
    repo: Repo,
    store: DocumentsStore,
    vector_index: VectorIndexProtocol,
    nova: NovaEmbeddingsProtocol,
    bedrock: BedrockMessages,
    guardrail: Guardrail,
    events: EventsPublisher,
    settings: Settings,
    conversation_id: str,
    project_id: str,
    owner_sub: str,
    assistant_message_id: str,
    user_text: str,
    pinned_document_ids: list[str],
    history: list[HistoryTurn],
    # `answering.stream.StreamPublisher`'s real-wall-clock cancellation-poll throttle — a
    # constructor seam, not a tuning knob (`services/answering/tests/test_turn.py` passes `0` so
    # a cancellation test doesn't need to sleep a full second).
    cancel_poll_interval_seconds: float = 1.0,
) -> Message:
    start = time.monotonic()
    channel = f"/conversations/{conversation_id}"
    seq = 0

    def _publish(event_type: str, data: dict[str, object]) -> None:
        nonlocal seq
        seq += 1
        events.publish(channel, event_type, {"messageId": assistant_message_id, **data}, seq=seq)

    def _next_seq() -> int:
        nonlocal seq
        seq += 1
        return seq

    def _should_cancel() -> bool:
        message = repo.get_message(conversation_id, assistant_message_id)
        return message is not None and message.cancel_requested

    def _persist(
        *,
        status: Literal["COMPLETE", "FAILED", "BLOCKED", "CANCELLED"],
        text: str,
        rewritten_query: str | None = None,
        retrieved: list[RetrievedRef] | None = None,
        citations: list[CitationRecord] | None = None,
        usage: Usage | None = None,
        latency_ms: LatencyMs | None = None,
    ) -> Message:
        message = repo.create_message(
            message_id=assistant_message_id,
            conversation_id=conversation_id,
            project_id=project_id,
            owner_sub=owner_sub,
            role="assistant",
            status=status,
            text=text,
            rewritten_query=rewritten_query,
            retrieved=retrieved,
            citations=citations,
            usage=usage,
            latency_ms=latency_ms,
        )
        repo.increment_message_count(conversation_id, project_id, by=1)
        return message

    _publish("message.started", {})

    try:
        # #1 rewrite (skipped, by rewrite_query itself, when history is empty).
        rewrite_start = time.monotonic()
        rewritten = rewrite_query(bedrock, settings, history=history, latest_message=user_text)
        rewrite_ms = int((time.monotonic() - rewrite_start) * 1000) if history else None
        if history:
            _publish("message.rewritten", {"rewrittenQuery": rewritten})

        # #2-5 embed, search, fuse, select, hydrate.
        retrieve_start = time.monotonic()
        result = retrieve(
            repo=repo,
            vector_index=vector_index,
            nova=nova,
            settings=settings,
            project_id=project_id,
            query_text=rewritten,
            pinned_document_ids=pinned_document_ids or None,
        )
        retrieve_ms = int((time.monotonic() - retrieve_start) * 1000)
        retrieved_refs = [
            RetrievedRef(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                page_number=hit.page_number,
                score=hit.score,
                kind=hit.kind,
            )
            for hit in result.retrieved
        ]

        # Filenames for both the `message.retrieval` sources list and the prompt's document
        # titles — computed once from the union of every id either needs.
        document_ids = {c.document_id for c in result.chunks} | {
            p.document_id for p in result.pages
        }
        filenames: dict[str, str] = {}
        for document_id in document_ids:
            document = repo.get_document(document_id)
            if document is not None:
                filenames[document_id] = document.filename

        _publish(
            "message.retrieval",
            {
                "sources": [
                    {
                        "documentId": page.document_id,
                        "filename": filenames.get(page.document_id, ""),
                        "pageNumber": page.page_number,
                        "score": page.fused_score,
                    }
                    for page in result.pages
                ]
            },
        )

        # #6 guard (INPUT) — docs/04's numbered step list runs this after retrieval, not before
        # the rewrite: the rewrite/retrieval calls are cheap next to generation, which is what
        # this gate exists to avoid paying for on a blocked message.
        if _guardrail_blocked(guardrail, user_text, source="INPUT"):
            _publish("message.blocked", {"reason": "guardrail_intervened"})
            return _persist(status="BLOCKED", text=_BLOCKED_TEXT, rewritten_query=rewritten)

        # #5 (cont'd) prompt assembly: page images for text-thin selected pages.
        thin = thin_pages(
            result.chunks, result.pages, thin_text_char_threshold=settings.thin_text_char_threshold
        )
        page_images: dict[tuple[str, int], bytes] = {}
        for page in thin:
            page_item = repo.get_page(page.document_id, page.page_number)
            if page_item is not None and page_item.s3 is not None:
                page_images[(page.document_id, page.page_number)] = store.get_object(
                    page_item.s3.embed
                )

        prompt_ctx = build_prompt_content(
            chunks=result.chunks,
            pages=result.pages,
            filenames=filenames,
            page_images=page_images,
        )
        messages = [
            {
                "role": "user",
                "content": [*prompt_ctx.content_blocks, {"type": "text", "text": user_text}],
            }
        ]

        # #7 generate — streamed to the client live (docs/04#streaming-to-the-client) via
        # `StreamPublisher`, which also polls `/cancel`'s flag between chunks.
        chunks_by_id = {c.chunk_id: c for c in result.chunks}
        publisher = StreamPublisher(
            events,
            channel=channel,
            message_id=assistant_message_id,
            next_seq=_next_seq,
            index_to_chunk=prompt_ctx.index_to_chunk,
            chunks_by_id=chunks_by_id,
            tick_seconds=settings.stream_batch_tick_ms / 1000,
            should_cancel=_should_cancel,
            cancel_poll_interval_seconds=cancel_poll_interval_seconds,
        )
        try:
            generated = generate(
                bedrock,
                settings,
                system=SYSTEM_PROMPT,
                messages=messages,
                on_event=publisher.handle_event,
            )
        finally:
            publisher.flush()

        # #8 map citations, accumulating the answer text and each text block's char span. This
        # is the authoritative pass — independent of, and unaffected by, whatever `StreamPublisher`
        # published live from the same raw events above (docs/04: "the persisted message is built
        # from the final message, not from the accumulated deltas").
        answer_text = ""
        citations: list[CitationRecord] = []
        running_offset = 0
        for block in generated.content:
            if block.get("type") != "text":
                continue
            block_text = str(block.get("text", ""))
            for raw_citation in block.get("citations", []):
                citation = Citation(
                    cited_text=raw_citation.get("cited_text", ""),
                    document_index=raw_citation.get("document_index", -1),
                    document_title=raw_citation.get("document_title", ""),
                    start_block_index=raw_citation.get("start_block_index", 0),
                    end_block_index=raw_citation.get("end_block_index", 0),
                )
                mapped = map_citation(
                    citation,
                    citation_id=f"c{len(citations)}",
                    index_to_chunk=prompt_ctx.index_to_chunk,
                    chunks_by_id=chunks_by_id,
                    span_start=running_offset,
                    span_end=running_offset + len(block_text),
                )
                if mapped is None:
                    logger.warning("dropped an unmappable citation")
                    continue
                citations.append(mapped)
            answer_text += block_text
            running_offset += len(block_text)

        # #9 guard (OUTPUT).
        if _guardrail_blocked(guardrail, answer_text, source="OUTPUT"):
            _publish("message.blocked", {"reason": "guardrail_intervened"})
            return _persist(status="BLOCKED", text=_BLOCKED_TEXT, rewritten_query=rewritten)

        total_ms = int((time.monotonic() - start) * 1000)
        usage = Usage(
            input_tokens=generated.input_tokens,
            output_tokens=generated.output_tokens,
            cache_read_input_tokens=generated.cache_read_input_tokens,
        )
        # `embed` has no separate timing — `retrieve()` folds embed+search+fuse into one call
        # with no internal instrumentation boundary; not worth adding one for a single field
        # Phase 8's tuning sweep, not Phase 5's exit criteria, actually needs.
        latency_ms = LatencyMs(
            rewrite=rewrite_ms,
            retrieve=retrieve_ms,
            first_token=generated.first_token_ms,
            total=total_ms,
        )

        # #10 persist.
        message = _persist(
            status="COMPLETE",
            text=answer_text,
            rewritten_query=rewritten,
            retrieved=retrieved_refs,
            citations=citations,
            usage=usage,
            latency_ms=latency_ms,
        )
        _publish(
            "message.completed",
            {
                "usage": usage.model_dump(by_alias=True),
                "latencyMs": latency_ms.model_dump(by_alias=True),
            },
        )
        return message
    except TurnCancelledError:
        logger.info("answer turn cancelled")
        _publish("message.failed", {"code": CANCELLED_CODE, "message": _CANCELLED_TEXT})
        return _persist(status="CANCELLED", text=_CANCELLED_TEXT)
    except Exception:
        logger.exception("answer turn failed")
        _publish("message.failed", {"code": "INTERNAL_ERROR", "message": _FAILED_TEXT})
        return _persist(status="FAILED", text=_FAILED_TEXT)
    finally:
        repo.release_lock(conversation_id)
