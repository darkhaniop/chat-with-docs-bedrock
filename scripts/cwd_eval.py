"""`uv run cwd-eval` — citation fidelity evaluation harness (docs/08-testing.md#citation-
fidelity-evaluation). Phase 4 implements `--retrieval-only` (Recall@10 against the eval question
set, `e2e/fixtures/eval/questions.json`); citation/generation metrics arrive in Phase 5 once
`services/answering/generate.py`/`citations.py` exist.

`evaluate_retrieval` takes adapter objects (so it's unit-testable against fakes — see
`services/answering/tests/test_cwd_eval.py`); `main` is the only place real boto3 clients get
constructed and the only place `questions.json` is read from disk.

The eval corpus (`e2e/fixtures/eval/questions.json`'s `corpus` list) must already be uploaded
and ingested into the project passed as `--project-id` — this script only measures retrieval
against a project that already has vectors, it does not upload or ingest anything itself.
Questions reference documents by their original filename (`expectedPages[].document`), not by
documentId (which is only assigned at upload time and differs per project) — `evaluate_retrieval`
resolves filename to documentId via `repo.list_all_documents`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import boto3

from answering.retrieve import retrieve
from common.bedrock.embeddings import NovaEmbeddings, NovaEmbeddingsProtocol
from common.config import Settings, get_settings
from common.repo import Repo
from common.vectors import VectorIndex, VectorIndexProtocol

QUESTIONS_FILE = (
    Path(__file__).resolve().parent.parent / "e2e" / "fixtures" / "eval" / "questions.json"
)


def load_questions(path: Path = QUESTIONS_FILE) -> list[dict[str, Any]]:
    payload: dict[str, Any] = json.loads(path.read_text())
    questions: list[dict[str, Any]] = payload["questions"]
    return questions


def evaluate_retrieval(
    *,
    repo: Repo,
    vector_index: VectorIndexProtocol,
    nova: NovaEmbeddingsProtocol,
    settings: Settings,
    project_id: str,
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Recall@10: the fraction of questions where at least one expected `(documentId,
    pageNumber)` appears anywhere in `RetrievalResult.retrieved` (the full topK-fused hit list,
    not just the final selected/capped set — Recall@10 measures the search step, selection is a
    separate concern already covered by `test_fusion.py`'s unit tests)."""
    filename_to_document_id = {
        d.filename: d.document_id for d in repo.list_all_documents(project_id)
    }

    rows: list[dict[str, Any]] = []
    hits = 0
    for question in questions:
        result = retrieve(
            repo=repo,
            vector_index=vector_index,
            nova=nova,
            settings=settings,
            project_id=project_id,
            query_text=question["question"],
        )
        retrieved_pages = {(hit.document_id, hit.page_number) for hit in result.retrieved}
        expected_pages = {
            (filename_to_document_id[p["document"]], p["page"])
            for p in question["expectedPages"]
            if p["document"] in filename_to_document_id
        }
        hit = bool(expected_pages & retrieved_pages)
        hits += hit
        rows.append({"id": question["id"], "question": question["question"], "hit": hit})

    total = len(questions)
    return {
        "recallAt10": (hits / total) if total else 0.0,
        "hits": hits,
        "total": total,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="cwd-eval: citation fidelity evaluation harness")
    parser.add_argument("--env", default="dev")
    parser.add_argument(
        "--project-id", required=True, help="Project the eval corpus was uploaded/ingested into"
    )
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        required=True,
        help="Only measure Recall@10 (Phase 4; the only mode implemented so far)",
    )
    args = parser.parse_args()

    settings = get_settings()
    dynamodb = boto3.client("dynamodb", region_name=settings.aws_region)
    repo = Repo(dynamodb, table_name=settings.table_name)

    account_id = boto3.client("sts").get_caller_identity()["Account"]
    s3vectors = boto3.client("s3vectors", region_name=settings.aws_region)
    vector_index = VectorIndex(
        settings, s3vectors, vector_bucket_name=settings.vector_bucket_name(account_id)
    )

    bedrock_runtime = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    nova = NovaEmbeddings(settings, bedrock_runtime)

    report = evaluate_retrieval(
        repo=repo,
        vector_index=vector_index,
        nova=nova,
        settings=settings,
        project_id=args.project_id,
        questions=load_questions(),
    )
    print(json.dumps(report, indent=2))
    print(f"\nRecall@10: {report['recallAt10']:.2%} ({report['hits']}/{report['total']})")


if __name__ == "__main__":
    main()
