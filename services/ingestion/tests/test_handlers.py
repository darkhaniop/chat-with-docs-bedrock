from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from common.storage import (
    blocks_key,
    chunks_key,
    embed_render_key,
    probe_key,
    render_key,
    source_key,
)
from common.testing.events import FakeEventsPublisher
from ingestion import deps, handlers

_FIXTURES = Path(__file__).parents[3] / "e2e" / "fixtures"


@pytest.fixture
def fake_events(monkeypatch: pytest.MonkeyPatch) -> FakeEventsPublisher:
    fake = FakeEventsPublisher()
    monkeypatch.setattr(deps, "get_events", lambda: fake)
    return fake


def _seed_document(*, content_type: str = "application/pdf") -> tuple[str, str]:
    repo = deps.get_repo()
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    document = repo.create_document(
        project_id=project.project_id,
        owner_sub="user-1",
        filename="doc.pdf",
        content_type=content_type,
        byte_size=1,
        kind="pdf" if content_type == "application/pdf" else "image",
    )
    repo.update_document(document.document_id, status="PROCESSING")
    return project.project_id, document.document_id


def _put_source(project_id: str, document_id: str, filename: str) -> None:
    store = deps.get_store()
    data = (_FIXTURES / filename).read_bytes()
    store.put_object(
        source_key(project_id, document_id, "pdf" if filename.endswith("pdf") else "jpg"),
        data,
        content_type="application/pdf" if filename.endswith("pdf") else "image/jpeg",
    )


# -- probe_handler --------------------------------------------------------------------------


def test_probe_handler_writes_probe_json_and_page_items(fake_events: FakeEventsPublisher) -> None:
    project_id, document_id = _seed_document()
    _put_source(project_id, document_id, "born-digital.pdf")

    result = handlers.probe_handler(
        {
            "projectId": project_id,
            "documentId": document_id,
            "s3Key": source_key(project_id, document_id, "pdf"),
            "contentType": "application/pdf",
        },
        context=None,  # type: ignore[arg-type]
    )

    assert result["pageCount"] == 1
    assert result["ocrPageCount"] == 0
    assert result["kind"] == "pdf"

    store = deps.get_store()
    probe_json = json.loads(store.get_object(probe_key(project_id, document_id)))
    assert isinstance(probe_json, list)
    assert len(probe_json) == 1
    assert probe_json[0]["textSource"] == "pdf"

    repo = deps.get_repo()
    page = repo.get_page(document_id, 1)
    assert page is not None
    assert page.text_source == "pdf"
    assert page.width == pytest.approx(612.0)

    document = repo.get_document(document_id)
    assert document is not None
    assert document.page_count == 1
    assert document.ingestion.started_at is not None

    assert any(e.event_type == "document.progress" for e in fake_events.events)


def test_probe_handler_on_a_scanned_pdf_marks_pages_textract(
    fake_events: FakeEventsPublisher,
) -> None:
    project_id, document_id = _seed_document()
    _put_source(project_id, document_id, "scanned.pdf")

    result = handlers.probe_handler(
        {
            "projectId": project_id,
            "documentId": document_id,
            "s3Key": source_key(project_id, document_id, "pdf"),
            "contentType": "application/pdf",
        },
        context=None,  # type: ignore[arg-type]
    )

    assert result["ocrPageCount"] == 1
    page = deps.get_repo().get_page(document_id, 1)
    assert page is not None
    assert page.text_source == "textract"


# -- page_handler ---------------------------------------------------------------------------


def _page_event(
    project_id: str,
    document_id: str,
    probe_result: dict[str, Any],
    probe_json: list[dict[str, Any]],
    *,
    index: int = 0,
) -> dict[str, Any]:
    """Mirrors what the Distributed Map's `itemSelector` assembles in
    `infra/lib/compute-stack.ts`: per-page fields from the `probe.json` array item, merged with
    per-document fields carried through the state machine's own data flow (Probe's return
    value), since `probe.json` itself is a bare array (`S3JsonItemReader` requires it)."""
    page = probe_json[index]
    return {
        "projectId": project_id,
        "documentId": document_id,
        "s3Key": probe_result["s3Key"],
        "kind": probe_result["kind"],
        "pageNumber": page["pageNumber"],
        "width": page["width"],
        "height": page["height"],
        "rotation": page["rotation"],
        "textSource": page["textSource"],
        "textDensity": page["textDensity"],
        "pageCount": probe_result["pageCount"],
    }


def test_page_handler_pdf_text_path_writes_renders_and_blocks(
    fake_events: FakeEventsPublisher,
) -> None:
    project_id, document_id = _seed_document()
    _put_source(project_id, document_id, "born-digital.pdf")
    probe_result = handlers.probe_handler(
        {
            "projectId": project_id,
            "documentId": document_id,
            "s3Key": source_key(project_id, document_id, "pdf"),
            "contentType": "application/pdf",
        },
        context=None,  # type: ignore[arg-type]
    )
    store = deps.get_store()
    probe_json = json.loads(store.get_object(probe_key(project_id, document_id)))

    event = _page_event(project_id, document_id, probe_result, probe_json)
    result = handlers.page_handler(event, context=None)  # type: ignore[arg-type]

    assert result["textSource"] == "pdf"
    assert result["lineCount"] > 0
    assert store.object_exists(render_key(project_id, document_id, 1))
    assert store.object_exists(embed_render_key(project_id, document_id, 1))
    block = json.loads(store.get_object(blocks_key(project_id, document_id, 1)))
    assert block["textSource"] == "pdf"
    assert len(block["lines"]) > 0

    page = deps.get_repo().get_page(document_id, 1)
    assert page is not None
    assert page.s3 is not None
    assert page.s3.display == render_key(project_id, document_id, 1)


class _ScriptedTextractClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def detect_document_text(self, Document: dict[str, Any]) -> dict[str, Any]:  # noqa: N803
        return self._response


def test_page_handler_textract_path_for_a_scanned_pdf(
    fake_events: FakeEventsPublisher, monkeypatch: pytest.MonkeyPatch
) -> None:
    from common.ocr import Textract

    project_id, document_id = _seed_document()
    _put_source(project_id, document_id, "scanned.pdf")
    probe_result = handlers.probe_handler(
        {
            "projectId": project_id,
            "documentId": document_id,
            "s3Key": source_key(project_id, document_id, "pdf"),
            "contentType": "application/pdf",
        },
        context=None,  # type: ignore[arg-type]
    )
    probe_json = json.loads(deps.get_store().get_object(probe_key(project_id, document_id)))

    fake_response = {
        "Blocks": [
            {
                "BlockType": "LINE",
                "Text": "Dear Ms. Alvarez, thank you for your letter regarding the file.",
                "Geometry": {
                    "BoundingBox": {"Left": 0.1, "Top": 0.1, "Width": 0.5, "Height": 0.05}
                },
            }
        ]
    }
    monkeypatch.setattr(
        deps, "get_textract", lambda: Textract(_ScriptedTextractClient(fake_response))
    )

    event = _page_event(project_id, document_id, probe_result, probe_json)
    result = handlers.page_handler(event, context=None)  # type: ignore[arg-type]

    assert result["textSource"] == "textract"
    assert result["lineCount"] == 1
    block = json.loads(deps.get_store().get_object(blocks_key(project_id, document_id, 1)))
    assert "Alvarez" in block["lines"][0]["text"]


def test_page_handler_publishes_progress_only_every_10th_page(
    fake_events: FakeEventsPublisher, monkeypatch: pytest.MonkeyPatch
) -> None:
    from common.ocr import Textract

    project_id, document_id = _seed_document(content_type="image/jpeg")
    store = deps.get_store()
    store.put_object(
        source_key(project_id, document_id, "jpg"),
        (_FIXTURES / "photograph.jpg").read_bytes(),
        content_type="image/jpeg",
    )
    monkeypatch.setattr(
        deps, "get_textract", lambda: Textract(_ScriptedTextractClient({"Blocks": []}))
    )

    event = {
        "projectId": project_id,
        "documentId": document_id,
        "s3Key": source_key(project_id, document_id, "jpg"),
        "kind": "image",
        "pageNumber": 10,
        "width": 1200.0,
        "height": 900.0,
        "rotation": 0,
        "textSource": "textract",
        "textDensity": 0.0,
        "pageCount": 20,
    }

    handlers.page_handler(event, context=None)  # type: ignore[arg-type]

    assert any(
        e.event_type == "document.progress" and e.data["pagesDone"] == 10
        for e in fake_events.events
    )


def test_page_handler_does_not_publish_progress_on_a_non_multiple_of_10(
    fake_events: FakeEventsPublisher, monkeypatch: pytest.MonkeyPatch
) -> None:
    from common.ocr import Textract

    project_id, document_id = _seed_document(content_type="image/jpeg")
    store = deps.get_store()
    store.put_object(
        source_key(project_id, document_id, "jpg"),
        (_FIXTURES / "photograph.jpg").read_bytes(),
        content_type="image/jpeg",
    )
    monkeypatch.setattr(
        deps, "get_textract", lambda: Textract(_ScriptedTextractClient({"Blocks": []}))
    )

    event = {
        "projectId": project_id,
        "documentId": document_id,
        "s3Key": source_key(project_id, document_id, "jpg"),
        "kind": "image",
        "pageNumber": 3,
        "width": 1200.0,
        "height": 900.0,
        "rotation": 0,
        "textSource": "textract",
        "textDensity": 0.0,
        "pageCount": 20,
    }

    handlers.page_handler(event, context=None)  # type: ignore[arg-type]

    assert fake_events.events == []


# -- chunk_handler ----------------------------------------------------------------------------


def test_chunk_handler_writes_chunk_items_and_jsonl(fake_events: FakeEventsPublisher) -> None:
    project_id, document_id = _seed_document()
    _put_source(project_id, document_id, "born-digital.pdf")
    probe_result = handlers.probe_handler(
        {
            "projectId": project_id,
            "documentId": document_id,
            "s3Key": source_key(project_id, document_id, "pdf"),
            "contentType": "application/pdf",
        },
        context=None,  # type: ignore[arg-type]
    )
    probe_json = json.loads(deps.get_store().get_object(probe_key(project_id, document_id)))
    event = _page_event(project_id, document_id, probe_result, probe_json)
    handlers.page_handler(event, context=None)  # type: ignore[arg-type]

    result = handlers.chunk_handler(
        {"projectId": project_id, "documentId": document_id, "pageCount": 1}, context=None
    )  # type: ignore[arg-type]

    assert result["chunkCount"] > 0
    store = deps.get_store()
    assert store.object_exists(chunks_key(project_id, document_id))
    lines = store.get_object(chunks_key(project_id, document_id)).decode().splitlines()
    assert len(lines) == result["chunkCount"]

    repo = deps.get_repo()
    first_chunk_id = json.loads(lines[0])["chunkId"]
    fetched = repo.get_chunk(document_id, first_chunk_id)
    assert fetched is not None
    assert fetched.page_number == 1
    assert len(fetched.sentences) > 0
    assert fetched.sentences[0].i == 0


# -- finalize_handler -------------------------------------------------------------------------


def test_finalize_handler_marks_ready_and_updates_counts(fake_events: FakeEventsPublisher) -> None:
    project_id, document_id = _seed_document()

    result = handlers.finalize_handler(
        {
            "projectId": project_id,
            "documentId": document_id,
            "pageCount": 5,
            "ocrPageCount": 1,
            "chunkCount": 12,
        },
        context=None,  # type: ignore[arg-type]
    )

    assert result["status"] == "READY"
    repo = deps.get_repo()
    document = repo.get_document(document_id)
    assert document is not None
    assert document.status == "READY"
    assert document.ingestion.chunk_count == 12
    assert document.ingestion.finished_at is not None

    project = repo.get_project(project_id)
    assert project is not None
    assert project.chunk_count == 12

    assert any(e.event_type == "document.ready" for e in fake_events.events)


# -- mark_failed_handler ----------------------------------------------------------------------


def test_mark_failed_handler_extracts_message_from_lambda_cause(
    fake_events: FakeEventsPublisher,
) -> None:
    project_id, document_id = _seed_document()
    cause = json.dumps(
        {"errorMessage": "Encrypted PDF: password required.", "errorType": "ProbeError"}
    )

    result = handlers.mark_failed_handler(
        {
            "projectId": project_id,
            "documentId": document_id,
            "error": {"Error": "ProbeError", "Cause": cause},
        },
        context=None,  # type: ignore[arg-type]
    )

    assert result["status"] == "FAILED"
    assert result["statusDetail"] == "Encrypted PDF: password required."

    document = deps.get_repo().get_document(document_id)
    assert document is not None
    assert document.status == "FAILED"
    assert document.status_detail == "Encrypted PDF: password required."
    assert any(e.event_type == "document.failed" for e in fake_events.events)


def test_mark_failed_handler_falls_back_when_cause_is_not_json(
    fake_events: FakeEventsPublisher,
) -> None:
    project_id, document_id = _seed_document()

    result = handlers.mark_failed_handler(
        {
            "projectId": project_id,
            "documentId": document_id,
            "error": {"Error": "States.TaskFailed", "Cause": "not json"},
        },
        context=None,  # type: ignore[arg-type]
    )

    assert result["statusDetail"] == "not json"


def test_mark_failed_handler_handles_a_missing_error_field(
    fake_events: FakeEventsPublisher,
) -> None:
    project_id, document_id = _seed_document()

    result = handlers.mark_failed_handler(
        {"projectId": project_id, "documentId": document_id}, context=None
    )  # type: ignore[arg-type]

    assert result["statusDetail"] == "Ingestion failed for an unknown reason."
