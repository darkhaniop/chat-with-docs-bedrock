"""`uv run cwd-eval` — citation fidelity evaluation harness (docs/08-testing.md#citation-
fidelity-evaluation). Phase 4 implemented `--retrieval-only` (Recall@10 against the eval question
set, `e2e/fixtures/eval/questions.json`); Phase 5 adds the full mode, which runs every question
through the real answer turn (`answering.turn.run_turn`) and reports citation page accuracy,
sentence accuracy, suspect rate, and latency.

`evaluate_retrieval`/`evaluate_full` take adapter objects (so they're unit-testable against
fakes — see `scripts/tests/test_cwd_eval.py`); `main` is the only place real boto3 clients get
constructed and the only place `questions.json` is read from disk.

The eval corpus (`e2e/fixtures/eval/questions.json`'s `corpus` list) must already be uploaded
and ingested into the project passed as `--project-id` — this script only measures against a
project that already has vectors, it does not upload or ingest anything itself. Questions
reference documents by their original filename (`expectedPages[].document`), not by documentId
(which is only assigned at upload time and differs per project) — both eval functions resolve
filename to documentId via `repo.list_all_documents`.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

import boto3

from answering.retrieve import retrieve
from answering.turn import run_turn
from common.bedrock.embeddings import NovaEmbeddings, NovaEmbeddingsProtocol
from common.bedrock.guardrail import Guardrail
from common.bedrock.messages import BedrockMessages
from common.config import Settings, get_settings
from common.repo import Repo, new_id
from common.storage import DocumentsStore
from common.vectors import VectorIndex, VectorIndexProtocol

QUESTIONS_FILE = (
    Path(__file__).resolve().parent.parent / "e2e" / "fixtures" / "eval" / "questions.json"
)

_EVAL_OWNER_SUB = "cwd-eval"


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


def evaluate_full(
    *,
    repo: Repo,
    store: DocumentsStore,
    vector_index: VectorIndexProtocol,
    nova: NovaEmbeddingsProtocol,
    bedrock: BedrockMessages,
    guardrail: Guardrail,
    settings: Settings,
    project_id: str,
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Runs every question through the real answer turn and reports the metrics
    docs/08-testing.md's "Citation fidelity evaluation" table names, with two documented
    substitutions where the fixture question set doesn't carry the ground truth the table's
    literal definition needs:

    - **Sentence accuracy** substitutes for "sentence-span accuracy (IoU >= 0.5)":
      `questions.json` records `expectedSentenceContains` (a substring), not a sentence index or
      rect, so there is nothing to compute an IoU against. A citation "gets" a question's
      sentence accuracy if it lands on an expected page *and* its `citedText` contains the
      expected substring.
    - **Uncited-claim rate** is not computed at all — docs/08 defines it as "sampled,
      hand-labelled," which is inherently a human task, not something this script can honestly
      automate. Left for a human reviewer sampling real transcripts.
    - **Mean cost per turn** is not computed — no Bedrock per-token pricing constants exist
      anywhere in this codebase yet (docs/09-operations.md's cost model defers real figures to
      Phase 8's Cost Explorer review). Mean input/output token counts are reported instead, as
      the honest, currently-available substitute.
    """
    filename_to_document_id = {
        d.filename: d.document_id for d in repo.list_all_documents(project_id)
    }
    conversation = repo.create_conversation(
        project_id=project_id, owner_sub=_EVAL_OWNER_SUB, title="cwd-eval", pinned_document_ids=[]
    )

    rows: list[dict[str, Any]] = []
    page_hits = 0
    sentence_hits = 0
    suspect_citations = 0
    total_citations = 0
    total_latency_ms: list[int] = []
    input_tokens: list[int] = []
    output_tokens: list[int] = []

    for question in questions:
        message = run_turn(
            repo=repo,
            store=store,
            vector_index=vector_index,
            nova=nova,
            bedrock=bedrock,
            guardrail=guardrail,
            settings=settings,
            conversation_id=conversation.conversation_id,
            project_id=project_id,
            owner_sub=_EVAL_OWNER_SUB,
            assistant_message_id=new_id(),
            user_text=question["question"],
            pinned_document_ids=[],
            history=[],
        )

        expected_pages = {
            (filename_to_document_id[p["document"]], p["page"])
            for p in question["expectedPages"]
            if p["document"] in filename_to_document_id
        }
        expected_substring = question.get("expectedSentenceContains")

        page_hit = any((c.document_id, c.page_number) in expected_pages for c in message.citations)
        sentence_hit = expected_substring is not None and any(
            (c.document_id, c.page_number) in expected_pages and expected_substring in c.cited_text
            for c in message.citations
        )
        page_hits += page_hit
        sentence_hits += sentence_hit
        total_citations += len(message.citations)
        suspect_citations += sum(1 for c in message.citations if c.suspect)
        if message.latency_ms is not None:
            total_latency_ms.append(message.latency_ms.total)
        if message.usage is not None:
            input_tokens.append(message.usage.input_tokens)
            output_tokens.append(message.usage.output_tokens)

        rows.append(
            {
                "id": question["id"],
                "question": question["question"],
                "status": message.status,
                "pageHit": page_hit,
                "sentenceHit": sentence_hit,
                "citationCount": len(message.citations),
            }
        )

    total = len(questions)

    def _percentile(values: list[int], fraction: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        index = min(len(ordered) - 1, int(len(ordered) * fraction))
        return float(ordered[index])

    return {
        "citationPageAccuracy": (page_hits / total) if total else 0.0,
        "sentenceAccuracy": (sentence_hits / total) if total else 0.0,
        "suspectRate": (suspect_citations / total_citations) if total_citations else 0.0,
        "totalCitations": total_citations,
        "p50LatencyMs": _percentile(total_latency_ms, 0.5),
        "p95LatencyMs": _percentile(total_latency_ms, 0.95),
        "meanInputTokens": statistics.fmean(input_tokens) if input_tokens else None,
        "meanOutputTokens": statistics.fmean(output_tokens) if output_tokens else None,
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
        help="Only measure Recall@10 (Phase 4). Default runs the full answer turn (Phase 5).",
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

    if args.retrieval_only:
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
        return

    s3 = boto3.client("s3", region_name=settings.aws_region)
    store = DocumentsStore(s3, settings, bucket_name=settings.documents_bucket_name(account_id))
    bedrock = BedrockMessages(settings, bedrock_runtime)
    guardrail = Guardrail(settings, bedrock_runtime)

    report = evaluate_full(
        repo=repo,
        store=store,
        vector_index=vector_index,
        nova=nova,
        bedrock=bedrock,
        guardrail=guardrail,
        settings=settings,
        project_id=args.project_id,
        questions=load_questions(),
    )
    print(json.dumps(report, indent=2))
    print(
        f"\nCitation page accuracy: {report['citationPageAccuracy']:.2%}"
        f"\nSentence accuracy: {report['sentenceAccuracy']:.2%}"
        f"\nSuspect rate: {report['suspectRate']:.2%} ({report['totalCitations']} citations)"
        f"\np50/p95 latency: {report['p50LatencyMs']}ms / {report['p95LatencyMs']}ms"
    )


if __name__ == "__main__":
    main()
