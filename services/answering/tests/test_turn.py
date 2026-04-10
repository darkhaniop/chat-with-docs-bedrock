"""`answering.turn.run_turn` end to end (docs/04-retrieval-and-citations.md "The answer turn,
end to end"), against fakes for every un-emulated service (Bedrock, S3 Vectors) plus a
moto-backed `Repo` — the shape docs/08-testing.md's fakes section describes.
"""

from __future__ import annotations

from typing import Any, Literal

from answering.turn import run_turn
from common.config import get_settings
from common.models import Chunk, Page, Sentence
from common.repo import Repo
from common.testing.embeddings import FakeNova
from common.testing.events import FakeEventsPublisher
from common.testing.vectors import FakeVectorIndex
from common.vectors import chunk_vector_key

_CITATION = {
    "type": "content_block_location",
    "cited_text": "94% uptime",
    "document_index": 0,
    "document_title": "report.pdf — page 1",
    "start_block_index": 0,
    "end_block_index": 1,
}

_GENERATE_EVENTS: list[dict[str, Any]] = [
    {"type": "message_start", "message": {"usage": {"input_tokens": 42}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "Uptime was 94% uptime in Q3."},
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "citations_delta", "citation": _CITATION},
    },
    {"type": "content_block_stop", "index": 0},
    {"type": "message_delta", "delta": {}, "usage": {"output_tokens": 8}},
]


class _StubBedrock:
    def __init__(self, *, rewrite_text: str | None = None) -> None:
        self._rewrite_text = rewrite_text
        self.stream_calls = 0
        self.create_calls = 0

    def create(self, **kwargs: Any) -> dict[str, Any]:
        self.create_calls += 1
        return {"content": [{"type": "text", "text": self._rewrite_text or ""}]}

    def stream(self, **kwargs: Any):  # noqa: ANN201 — mirrors BedrockMessages.stream
        self.stream_calls += 1
        yield from _GENERATE_EVENTS


class _StubGuardrailResult:
    def __init__(self, blocked: bool) -> None:
        self.blocked = blocked


class _StubGuardrail:
    def __init__(
        self,
        *,
        block_input: bool = False,
        block_output: bool = False,
        raise_on: set[str] | None = None,
    ) -> None:
        self._block_input = block_input
        self._block_output = block_output
        self._raise_on = raise_on or set()
        self.calls: list[str] = []

    def apply(self, text: str, *, source: Literal["INPUT", "OUTPUT"]) -> _StubGuardrailResult:
        self.calls.append(source)
        if source in self._raise_on:
            raise RuntimeError("guardrail unavailable")
        blocked = self._block_input if source == "INPUT" else self._block_output
        return _StubGuardrailResult(blocked)


class _StubStore:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self._objects = objects or {}

    def get_object(self, key: str) -> bytes:
        return self._objects[key]


def _seed_project_document_and_chunk(
    repo: Repo, *, chunk_text: str
) -> tuple[str, str, str, FakeVectorIndex]:
    project = repo.create_project(owner_sub="user-1", name="Ashford", description="")
    conversation = repo.create_conversation(
        project_id=project.project_id, owner_sub="user-1", title="", pinned_document_ids=[]
    )
    document = repo.create_document(
        project_id=project.project_id,
        owner_sub="user-1",
        filename="report.pdf",
        content_type="application/pdf",
        byte_size=10,
        kind="pdf",
    )
    repo.put_page(
        Page(
            document_id=document.document_id,
            page_number=1,
            width=612.0,
            height=792.0,
            text_source="pdf",
            text_density=0.5,
        )
    )
    chunk = Chunk(
        chunk_id="chunk-1",
        document_id=document.document_id,
        project_id=project.project_id,
        page_number=1,
        ordinal=0,
        text=chunk_text,
        sentences=[Sentence(i=0, text=chunk_text, rects=[(72.0, 640.0, 400.0, 655.0)])],
        token_estimate=50,
        created_at="2026-01-01T00:00:00Z",
    )
    repo.batch_write_chunks([chunk])

    settings = get_settings()
    vector_index = FakeVectorIndex()
    nova = FakeNova(settings=settings)
    index_name = settings.vector_index_name(project.project_id)
    vector_index.create_index_if_missing(index_name, non_filterable_metadata_keys=["preview"])
    vector_index.put_vectors(
        index_name,
        [
            (
                chunk_vector_key(document.document_id, chunk.chunk_id),
                nova.embed_text("seed", purpose="GENERIC_INDEX"),
                {
                    "documentId": document.document_id,
                    "pageNumber": 1,
                    "kind": "text",
                    "chunkId": chunk.chunk_id,
                },
            )
        ],
    )
    return project.project_id, document.document_id, conversation.conversation_id, vector_index


def _run(
    repo: Repo,
    *,
    vector_index: FakeVectorIndex,
    conversation_id: str,
    project_id: str,
    guardrail: _StubGuardrail | None = None,
    bedrock: _StubBedrock | None = None,
    store: _StubStore | None = None,
    history: list[tuple[str, str]] | None = None,
    events: FakeEventsPublisher | None = None,
    assistant_message_id: str = "assistant-1",
    cancel_poll_interval_seconds: float = 1.0,
) -> Any:
    settings = get_settings()
    return run_turn(
        repo=repo,
        store=store or _StubStore(),
        vector_index=vector_index,
        nova=FakeNova(settings=settings),
        bedrock=bedrock or _StubBedrock(),
        guardrail=guardrail or _StubGuardrail(),
        events=events or FakeEventsPublisher(),
        settings=settings,
        conversation_id=conversation_id,
        project_id=project_id,
        owner_sub="user-1",
        assistant_message_id=assistant_message_id,
        user_text="What was Q3 uptime?",
        pinned_document_ids=[],
        history=history or [],
        cancel_poll_interval_seconds=cancel_poll_interval_seconds,
    )


def test_happy_path_persists_a_complete_message_with_a_mapped_citation(repo: Repo) -> None:
    project_id, document_id, conversation_id, vector_index = _seed_project_document_and_chunk(
        repo, chunk_text="Uptime was 94% uptime in Q3, a new record for the facility."
    )
    bedrock = _StubBedrock()
    events = FakeEventsPublisher()

    message = _run(
        repo,
        vector_index=vector_index,
        conversation_id=conversation_id,
        project_id=project_id,
        bedrock=bedrock,
        events=events,
    )

    assert message.status == "COMPLETE"
    assert message.text == "Uptime was 94% uptime in Q3."
    assert bedrock.stream_calls == 1
    [citation] = message.citations
    assert citation.document_id == document_id
    assert citation.page_number == 1
    assert citation.chunk_id == "chunk-1"
    assert citation.suspect is False
    assert len(message.retrieved) == 1
    assert message.usage is not None
    assert message.usage.input_tokens == 42
    assert message.usage.output_tokens == 8
    assert message.latency_ms is not None
    assert message.latency_ms.first_token is not None

    # The lock must be released and the assistant message's own count bump applied.
    conversation = repo.get_conversation(conversation_id)
    assert conversation is not None
    assert conversation.active_message_id is None
    assert conversation.message_count == 1

    # docs/05-api-contracts.md#appsync-events: the conversation-channel event vocabulary, in
    # order, with a strictly increasing `seq` on this one channel.
    channel = f"/conversations/{conversation_id}"
    assert all(e.channel == channel for e in events.events)
    types = [e.event_type for e in events.events]
    # `message.citation` precedes `message.delta` here — the `citations_delta` event arrives
    # before `content_block_stop` flushes the buffered text (docs/04: "a citation can be
    # published before the text block it belongs to has finished").
    assert types == [
        "message.started",
        "message.retrieval",
        "message.citation",
        "message.delta",
        "message.completed",
    ]
    seqs = [e.seq for e in events.events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)
    delta_event = next(e for e in events.events if e.event_type == "message.delta")
    assert delta_event.data["text"] == "Uptime was 94% uptime in Q3."
    completed_event = events.events[-1]
    assert completed_event.data["usage"]["outputTokens"] == 8


def test_first_message_in_a_conversation_skips_rewrite(repo: Repo) -> None:
    project_id, _document_id, conversation_id, vector_index = _seed_project_document_and_chunk(
        repo, chunk_text="Uptime was 94% uptime in Q3."
    )
    bedrock = _StubBedrock()

    message = _run(
        repo,
        vector_index=vector_index,
        conversation_id=conversation_id,
        project_id=project_id,
        bedrock=bedrock,
        history=[],
    )

    assert message.status == "COMPLETE"
    assert message.rewritten_query == "What was Q3 uptime?"
    assert message.latency_ms is not None
    assert message.latency_ms.rewrite is None
    assert bedrock.create_calls == 0


def test_blocked_input_never_calls_generate(repo: Repo) -> None:
    project_id, _document_id, conversation_id, vector_index = _seed_project_document_and_chunk(
        repo, chunk_text="Uptime was 94% uptime in Q3."
    )
    bedrock = _StubBedrock()
    guardrail = _StubGuardrail(block_input=True)

    message = _run(
        repo,
        vector_index=vector_index,
        conversation_id=conversation_id,
        project_id=project_id,
        bedrock=bedrock,
        guardrail=guardrail,
    )

    assert message.status == "BLOCKED"
    assert bedrock.stream_calls == 0
    assert message.citations == []
    conversation = repo.get_conversation(conversation_id)
    assert conversation is not None
    assert conversation.active_message_id is None


def test_blocked_output_drops_citations_even_though_generation_ran(repo: Repo) -> None:
    project_id, _document_id, conversation_id, vector_index = _seed_project_document_and_chunk(
        repo, chunk_text="Uptime was 94% uptime in Q3."
    )
    bedrock = _StubBedrock()
    guardrail = _StubGuardrail(block_output=True)

    message = _run(
        repo,
        vector_index=vector_index,
        conversation_id=conversation_id,
        project_id=project_id,
        bedrock=bedrock,
        guardrail=guardrail,
    )

    assert message.status == "BLOCKED"
    assert bedrock.stream_calls == 1
    assert message.citations == []


def test_guardrail_exception_fails_open_and_the_turn_still_completes(repo: Repo) -> None:
    project_id, _document_id, conversation_id, vector_index = _seed_project_document_and_chunk(
        repo, chunk_text="Uptime was 94% uptime in Q3."
    )
    guardrail = _StubGuardrail(raise_on={"INPUT", "OUTPUT"})

    message = _run(
        repo,
        vector_index=vector_index,
        conversation_id=conversation_id,
        project_id=project_id,
        guardrail=guardrail,
    )

    assert message.status == "COMPLETE"
    assert set(guardrail.calls) == {"INPUT", "OUTPUT"}


def test_an_exception_mid_turn_persists_a_failed_message_and_still_releases_the_lock(
    repo: Repo,
) -> None:
    project_id, _document_id, conversation_id, vector_index = _seed_project_document_and_chunk(
        repo, chunk_text="Uptime was 94% uptime in Q3."
    )

    class _ExplodingBedrock(_StubBedrock):
        def stream(self, **kwargs: Any):  # noqa: ANN201
            raise RuntimeError("Bedrock is down")
            yield  # pragma: no cover - unreachable, satisfies the generator protocol

    message = _run(
        repo,
        vector_index=vector_index,
        conversation_id=conversation_id,
        project_id=project_id,
        bedrock=_ExplodingBedrock(),
    )

    assert message.status == "FAILED"
    conversation = repo.get_conversation(conversation_id)
    assert conversation is not None
    assert conversation.active_message_id is None


def test_cancellation_between_chunks_persists_a_cancelled_message(repo: Repo) -> None:
    project_id, _document_id, conversation_id, vector_index = _seed_project_document_and_chunk(
        repo, chunk_text="Uptime was 94% uptime in Q3."
    )
    # The placeholder assistant message `api.conversations.post_message` writes before enqueuing
    # (docs/05-api-contracts.md) — `StreamPublisher.should_cancel` reads this same item.
    repo.create_message(
        message_id="assistant-1",
        conversation_id=conversation_id,
        project_id=project_id,
        owner_sub="user-1",
        role="assistant",
        status="STREAMING",
        text="",
    )
    assert repo.request_cancel(conversation_id, "assistant-1") is True

    events = FakeEventsPublisher()
    message = _run(
        repo,
        vector_index=vector_index,
        conversation_id=conversation_id,
        project_id=project_id,
        events=events,
        assistant_message_id="assistant-1",
        # Real-time throttled to 1s by default (docs/answering/stream.py) — 0 makes the fake
        # bedrock stream's very first text delta trip the check without a real sleep.
        cancel_poll_interval_seconds=0,
    )

    assert message.status == "CANCELLED"
    conversation = repo.get_conversation(conversation_id)
    assert conversation is not None
    assert conversation.active_message_id is None
    assert events.events[-1].event_type == "message.failed"
    assert events.events[-1].data["code"] == "CANCELLED"


def test_no_retrieved_documents_still_generates_and_notes_it_in_the_prompt(repo: Repo) -> None:
    project = repo.create_project(owner_sub="user-1", name="Empty", description="")
    conversation = repo.create_conversation(
        project_id=project.project_id, owner_sub="user-1", title="", pinned_document_ids=[]
    )
    settings = get_settings()
    vector_index = FakeVectorIndex()
    vector_index.create_index_if_missing(
        settings.vector_index_name(project.project_id), non_filterable_metadata_keys=["preview"]
    )
    bedrock = _StubBedrock()

    message = _run(
        repo,
        vector_index=vector_index,
        conversation_id=conversation.conversation_id,
        project_id=project.project_id,
        bedrock=bedrock,
    )

    assert message.status == "COMPLETE"
    assert bedrock.stream_calls == 1
    assert message.retrieved == []
