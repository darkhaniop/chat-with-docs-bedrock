"""`scripts/cwd_eval.py`'s pure `evaluate_retrieval` against fakes, plus `load_questions`
against the real committed `e2e/fixtures/eval/questions.json` (catches a malformed-JSON or
schema-drift regression in the eval corpus itself, not just the harness code)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Literal

import boto3
import pytest
from moto import mock_aws
from scripts.cwd_eval import evaluate_full, evaluate_retrieval, load_questions

from common.config import get_settings
from common.repo import Repo
from common.testing.embeddings import FakeNova
from common.testing.events import FakeEventsPublisher
from common.testing.vectors import FakeVectorIndex

_PROJECT_ID = "proj1"

_CITATION = {
    "type": "content_block_location",
    "cited_text": "94% uptime",
    "document_index": 0,
    "document_title": "born-digital.pdf — page 1",
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
    def create(self, **kwargs: Any) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": ""}]}

    def stream(self, **kwargs: Any):  # noqa: ANN201 — mirrors BedrockMessages.stream
        yield from _GENERATE_EVENTS


class _StubGuardrailResult:
    blocked = False


class _StubGuardrail:
    def apply(self, text: str, *, source: Literal["INPUT", "OUTPUT"]) -> _StubGuardrailResult:
        return _StubGuardrailResult()


class _StubStore:
    def get_object(self, key: str) -> bytes:
        raise AssertionError("no thin page in this fixture should need an image")


@pytest.fixture
def repo() -> Iterator[Repo]:
    with mock_aws():
        settings = get_settings()
        dynamodb = boto3.client("dynamodb", region_name=settings.aws_region)
        dynamodb.create_table(
            TableName=settings.table_name,
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield Repo(dynamodb, table_name=settings.table_name)


def test_load_questions_reads_the_committed_eval_corpus() -> None:
    questions = load_questions()
    assert len(questions) >= 20
    ids = [q["id"] for q in questions]
    assert len(ids) == len(set(ids))  # no duplicate ids
    for q in questions:
        assert q["question"]
        assert q["expectedPages"]
    assert any("chart" in q.get("tags", []) for q in questions)
    assert any("scanned" in q.get("tags", []) for q in questions)


def test_evaluate_retrieval_counts_a_hit_when_an_expected_page_is_retrieved(repo: Repo) -> None:
    settings = get_settings()
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    document = repo.create_document(
        project_id=project.project_id,
        owner_sub="user-1",
        filename="born-digital.pdf",
        content_type="application/pdf",
        byte_size=1,
        kind="pdf",
    )
    vector_index = FakeVectorIndex()
    index_name = settings.vector_index_name(project.project_id)
    vector_index.create_index_if_missing(index_name, non_filterable_metadata_keys=[])
    nova = FakeNova(settings)
    query_vector = nova.embed_text("What was the uptime?", purpose="GENERIC_RETRIEVAL")
    vector_index.put_vectors(
        index_name,
        [
            (
                f"{document.document_id}:p0001",
                query_vector,
                {"documentId": document.document_id, "pageNumber": 1, "kind": "page"},
            )
        ],
    )
    questions = [
        {
            "id": "q1",
            "question": "What was the uptime?",
            "expectedPages": [{"document": "born-digital.pdf", "page": 1}],
        }
    ]

    report = evaluate_retrieval(
        repo=repo,
        vector_index=vector_index,
        nova=nova,
        settings=settings,
        project_id=project.project_id,
        questions=questions,
    )

    assert report["hits"] == 1
    assert report["total"] == 1
    assert report["recallAt10"] == 1.0
    assert report["rows"][0]["hit"] is True


def test_evaluate_retrieval_counts_a_miss_when_nothing_matches(repo: Repo) -> None:
    settings = get_settings()
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    repo.create_document(
        project_id=project.project_id,
        owner_sub="user-1",
        filename="born-digital.pdf",
        content_type="application/pdf",
        byte_size=1,
        kind="pdf",
    )
    vector_index = FakeVectorIndex()
    vector_index.create_index_if_missing(
        settings.vector_index_name(project.project_id), non_filterable_metadata_keys=[]
    )
    questions = [
        {
            "id": "q1",
            "question": "What was the uptime?",
            "expectedPages": [{"document": "born-digital.pdf", "page": 1}],
        }
    ]

    report = evaluate_retrieval(
        repo=repo,
        vector_index=vector_index,
        nova=FakeNova(settings),
        settings=settings,
        project_id=project.project_id,
        questions=questions,
    )

    assert report["hits"] == 0
    assert report["recallAt10"] == 0.0


def test_evaluate_retrieval_handles_a_question_whose_document_was_never_uploaded(
    repo: Repo,
) -> None:
    settings = get_settings()
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    vector_index = FakeVectorIndex()
    vector_index.create_index_if_missing(
        settings.vector_index_name(project.project_id), non_filterable_metadata_keys=[]
    )
    questions = [
        {
            "id": "q1",
            "question": "irrelevant",
            "expectedPages": [{"document": "not-uploaded.pdf", "page": 1}],
        }
    ]

    report = evaluate_retrieval(
        repo=repo,
        vector_index=vector_index,
        nova=FakeNova(settings),
        settings=settings,
        project_id=project.project_id,
        questions=questions,
    )

    assert report["hits"] == 0  # not a crash — just an unresolvable/unmatched question


def _seed_document_with_one_chunk(repo: Repo, settings: Any) -> tuple[str, FakeVectorIndex]:
    from common.models import Chunk, Page, Sentence
    from common.vectors import chunk_vector_key

    project = repo.create_project(owner_sub="user-1", name="p", description="")
    document = repo.create_document(
        project_id=project.project_id,
        owner_sub="user-1",
        filename="born-digital.pdf",
        content_type="application/pdf",
        byte_size=1,
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
        text="Uptime was 94% uptime in Q3, a new record for the facility.",
        sentences=[
            Sentence(
                i=0,
                text="Uptime was 94% uptime in Q3, a new record for the facility.",
                rects=[(72.0, 640.0, 400.0, 655.0)],
            )
        ],
        token_estimate=50,
        created_at="2026-01-01T00:00:00Z",
    )
    repo.batch_write_chunks([chunk])

    nova = FakeNova(settings)
    vector_index = FakeVectorIndex()
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
    return project.project_id, vector_index


def test_evaluate_full_reports_page_and_sentence_accuracy(repo: Repo) -> None:
    settings = get_settings()
    project_id, vector_index = _seed_document_with_one_chunk(repo, settings)
    questions = [
        {
            "id": "q1",
            "question": "What was the facility's uptime in Q3?",
            "expectedPages": [{"document": "born-digital.pdf", "page": 1}],
            "expectedSentenceContains": "94% uptime",
        }
    ]

    report = evaluate_full(
        repo=repo,
        store=_StubStore(),
        vector_index=vector_index,
        nova=FakeNova(settings),
        bedrock=_StubBedrock(),
        guardrail=_StubGuardrail(),
        events=FakeEventsPublisher(),
        settings=settings,
        project_id=project_id,
        questions=questions,
    )

    assert report["citationPageAccuracy"] == 1.0
    assert report["sentenceAccuracy"] == 1.0
    assert report["suspectRate"] == 0.0
    assert report["totalCitations"] == 1
    assert report["meanInputTokens"] == 42
    assert report["meanOutputTokens"] == 8
    assert report["p50LatencyMs"] is not None
    assert report["rows"][0]["status"] == "COMPLETE"


def test_evaluate_full_reports_a_miss_when_the_expected_sentence_isnt_quoted(repo: Repo) -> None:
    settings = get_settings()
    project_id, vector_index = _seed_document_with_one_chunk(repo, settings)
    questions = [
        {
            "id": "q1",
            "question": "What was the facility's uptime in Q3?",
            "expectedPages": [{"document": "born-digital.pdf", "page": 1}],
            "expectedSentenceContains": "something never actually said",
        }
    ]

    report = evaluate_full(
        repo=repo,
        store=_StubStore(),
        vector_index=vector_index,
        nova=FakeNova(settings),
        bedrock=_StubBedrock(),
        guardrail=_StubGuardrail(),
        events=FakeEventsPublisher(),
        settings=settings,
        project_id=project_id,
        questions=questions,
    )

    assert report["citationPageAccuracy"] == 1.0
    assert report["sentenceAccuracy"] == 0.0
