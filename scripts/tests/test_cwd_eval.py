"""`scripts/cwd_eval.py`'s pure `evaluate_retrieval` against fakes, plus `load_questions`
against the real committed `e2e/fixtures/eval/questions.json` (catches a malformed-JSON or
schema-drift regression in the eval corpus itself, not just the harness code)."""

from __future__ import annotations

from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws
from scripts.cwd_eval import evaluate_retrieval, load_questions

from common.config import get_settings
from common.repo import Repo
from common.testing.embeddings import FakeNova
from common.testing.vectors import FakeVectorIndex

_PROJECT_ID = "proj1"


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
