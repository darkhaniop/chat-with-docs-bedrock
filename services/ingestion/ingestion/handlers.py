"""Step Functions task handlers for the ingestion pipeline.

Each function here is one Lambda's `CMD` entry point: parse the state input, call into the pure
modules (`probe`, `render`, `extract`, `ocr`, `chunk`), persist via
`common.repo`/`common.storage`, publish a progress event, and return the small JSON output the
next state needs.
"""

from __future__ import annotations

import io
import json
from typing import Any, cast

import fitz  # PyMuPDF
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext
from PIL import Image

from common.config import get_settings
from common.models import Chunk, DocumentKind, Page, PageRenders, Sentence, TextSource
from common.repo import new_id, now_iso
from common.storage import blocks_key, chunks_key, embed_render_key, probe_key, render_key
from ingestion import deps
from ingestion.chunk import ChunkDraft, assemble_chunks, segment_page_sentences
from ingestion.chunk import Line as ChunkLine
from ingestion.extract import extract_pdf_lines
from ingestion.ocr import OcrLine, convert_lines_to_canonical, detect_lines_with_retry
from ingestion.probe import probe as run_probe
from ingestion.probe import to_probe_json
from ingestion.render import render_image_document, render_pdf_page

logger = Logger(service="ingestion")

_PROGRESS_EVERY_N_PAGES = 10


def _project_channel(project_id: str) -> str:
    return f"/projects/{project_id}"


# -- ingest-probe --------------------------------------------------------------------------------


def probe_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    project_id, document_id = event["projectId"], event["documentId"]
    content_type = event["contentType"]
    s3_key = event["s3Key"]
    logger.append_keys(projectId=project_id, documentId=document_id)

    settings = get_settings()
    repo = deps.get_repo()
    store = deps.get_store()
    events = deps.get_events()

    data = store.get_object(s3_key)

    result = run_probe(data, content_type, settings)  # ProbeError propagates to Step Functions.

    store.put_object(
        probe_key(project_id, document_id),
        json.dumps(to_probe_json(result)).encode(),
        content_type="application/json",
    )
    for page in result.pages:
        repo.put_page(
            Page(
                document_id=document_id,
                page_number=page.page_number,
                width=page.width,
                height=page.height,
                rotation=page.rotation,
                text_source=page.text_source,
                text_density=page.text_density,
            )
        )
    repo.update_document(document_id, page_count=result.page_count, kind=result.kind)
    repo.update_document_ingestion(document_id, started_at=now_iso())

    events.publish(
        _project_channel(project_id),
        "document.progress",
        {
            "documentId": document_id,
            "stage": "probe",
            "pagesDone": 0,
            "pagesTotal": result.page_count,
        },
        seq=0,
    )
    return {
        "projectId": project_id,
        "documentId": document_id,
        "contentType": content_type,
        "s3Key": s3_key,
        "kind": result.kind,
        "pageCount": result.page_count,
        "ocrPageCount": result.ocr_page_count,
        "probeKey": probe_key(project_id, document_id),
    }


# -- ingest-page (Distributed Map iteration) ------------------------------------------------------


def _render_and_lines_for_page(
    *,
    kind: DocumentKind,
    source_bytes: bytes,
    page_number: int,
    width: float,
    height: float,
    rotation: int,
    text_source: TextSource,
    settings: Any,
) -> tuple[Any, list[ChunkLine], TextSource]:
    """Returns `(PageRenders, lines, effective_text_source)`. `effective_text_source` can
    downgrade to `"none"` if OCR/extraction genuinely produced nothing usable - the caller
    persists whatever comes back.
    """
    if kind == "pdf":
        doc = fitz.open(stream=source_bytes, filetype="pdf")
        try:
            page = doc[page_number - 1]
            renders = render_pdf_page(
                page,
                display_dpi=settings.display_render_dpi,
                display_max_long_edge_px=settings.display_render_max_long_edge_px,
                embed_max_long_edge_px=settings.embed_render_max_long_edge_px,
            )
            if text_source == "pdf":
                lines = [
                    ChunkLine(text=line.text, rect=line.rect) for line in extract_pdf_lines(page)
                ]
                return renders, lines, "pdf" if lines else "none"
        finally:
            doc.close()
    else:
        renders = render_image_document(
            source_bytes,
            display_max_long_edge_px=settings.display_render_max_long_edge_px,
            embed_max_long_edge_px=settings.embed_render_max_long_edge_px,
        )

    # textSource == "textract": OCR the embed render we just produced.
    textract = deps.get_textract()
    with Image.open(io.BytesIO(renders.embed_jpg)) as embed_image:
        embed_width_px, embed_height_px = embed_image.size
    ocr_lines = detect_lines_with_retry(textract, renders.embed_jpg)
    canonical_lines: list[OcrLine] = convert_lines_to_canonical(
        ocr_lines,
        embed_width_px=embed_width_px,
        embed_height_px=embed_height_px,
        page_width_pt=width,
        page_height_pt=height,
    )
    lines = [ChunkLine(text=line.text, rect=line.rect) for line in canonical_lines]
    return renders, lines, "textract" if lines else "none"


def page_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    project_id, document_id = event["projectId"], event["documentId"]
    page_number = event["pageNumber"]
    kind = cast(DocumentKind, event["kind"])
    s3_key = event["s3Key"]
    width, height, rotation = event["width"], event["height"], event["rotation"]
    text_source = cast(TextSource, event["textSource"])
    pages_total = event["pageCount"]
    logger.append_keys(projectId=project_id, documentId=document_id, pageNumber=page_number)

    settings = get_settings()
    repo = deps.get_repo()
    store = deps.get_store()
    events = deps.get_events()

    source_bytes = store.get_object(s3_key)

    renders, lines, effective_text_source = _render_and_lines_for_page(
        kind=kind,
        source_bytes=source_bytes,
        page_number=page_number,
        width=width,
        height=height,
        rotation=rotation,
        text_source=text_source,
        settings=settings,
    )

    store.put_object(
        render_key(project_id, document_id, page_number),
        renders.display_png,
        content_type="image/png",
    )
    store.put_object(
        embed_render_key(project_id, document_id, page_number),
        renders.embed_jpg,
        content_type="image/jpeg",
    )
    store.put_object(
        blocks_key(project_id, document_id, page_number),
        json.dumps(
            {
                "pageNumber": page_number,
                "width": width,
                "height": height,
                "textSource": effective_text_source,
                "lines": [{"text": line.text, "rect": list(line.rect)} for line in lines],
            }
        ).encode(),
        content_type="application/json",
    )
    repo.put_page(
        Page(
            document_id=document_id,
            page_number=page_number,
            width=width,
            height=height,
            rotation=rotation,
            text_source=effective_text_source,
            text_density=event.get("textDensity", 0.0),
            s3=PageRenders(
                display=render_key(project_id, document_id, page_number),
                embed=embed_render_key(project_id, document_id, page_number),
            ),
        )
    )

    if page_number % _PROGRESS_EVERY_N_PAGES == 0:
        events.publish(
            _project_channel(project_id),
            "document.progress",
            {
                "documentId": document_id,
                "stage": "pages",
                "pagesDone": page_number,
                "pagesTotal": pages_total,
            },
            seq=page_number,
        )

    return {"pageNumber": page_number, "textSource": effective_text_source, "lineCount": len(lines)}


# -- ingest-chunk ---------------------------------------------------------------------------------


def _chunk_draft_to_model(
    draft: ChunkDraft, *, project_id: str, document_id: str, page_number: int
) -> Chunk:
    return Chunk(
        chunk_id=new_id(),
        document_id=document_id,
        project_id=project_id,
        page_number=page_number,
        ordinal=draft.ordinal,
        text=draft.text,
        sentences=[
            Sentence(i=i, text=s.text, rects=s.rects) for i, s in enumerate(draft.sentences)
        ],
        token_estimate=draft.token_estimate,
        created_at=now_iso(),
    )


def chunk_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    project_id, document_id = event["projectId"], event["documentId"]
    page_count = event["pageCount"]
    logger.append_keys(projectId=project_id, documentId=document_id)

    settings = get_settings()
    repo = deps.get_repo()
    store = deps.get_store()
    events = deps.get_events()

    all_chunks: list[Chunk] = []
    for page_number in range(1, page_count + 1):
        block = json.loads(store.get_object(blocks_key(project_id, document_id, page_number)))
        lines = [ChunkLine(text=line["text"], rect=tuple(line["rect"])) for line in block["lines"]]
        sentences = segment_page_sentences(lines, sentence_max_chars=settings.sentence_max_chars)
        drafts = assemble_chunks(sentences, settings)
        all_chunks.extend(
            _chunk_draft_to_model(
                draft, project_id=project_id, document_id=document_id, page_number=page_number
            )
            for draft in drafts
        )

    if all_chunks:
        repo.batch_write_chunks(all_chunks)
        store.put_object(
            chunks_key(project_id, document_id),
            "\n".join(json.dumps(c.model_dump(by_alias=True)) for c in all_chunks).encode(),
            content_type="application/x-ndjson",
        )

    events.publish(
        _project_channel(project_id),
        "document.progress",
        {
            "documentId": document_id,
            "stage": "chunk",
            "pagesDone": page_count,
            "pagesTotal": page_count,
        },
        seq=page_count + 1,
    )
    return {"chunkCount": len(all_chunks)}


# -- ingest-finalize ------------------------------------------------------------------------------


def finalize_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    project_id, document_id = event["projectId"], event["documentId"]
    page_count, ocr_page_count, chunk_count = (
        event["pageCount"],
        event["ocrPageCount"],
        event["chunkCount"],
    )
    logger.append_keys(projectId=project_id, documentId=document_id)

    repo = deps.get_repo()
    events = deps.get_events()

    repo.update_document(document_id, status="READY", status_detail=None, page_count=page_count)
    repo.update_document_ingestion(
        document_id, finished_at=now_iso(), ocr_pages=ocr_page_count, chunk_count=chunk_count
    )
    repo.increment_chunk_count(project_id, by=chunk_count)

    events.publish(
        _project_channel(project_id),
        "document.ready",
        {
            "documentId": document_id,
            "pageCount": page_count,
            "chunkCount": chunk_count,
            "ocrPages": ocr_page_count,
        },
        seq=page_count + 2,
    )
    return {"status": "READY"}


# -- MarkFailed -------------------------------------------------------------------------------


def _status_detail_from_error(error: dict[str, Any] | None) -> str:
    if not error:
        return "Ingestion failed for an unknown reason."
    cause = error.get("Cause")
    if cause:
        try:
            parsed = json.loads(cause)
            message = parsed.get("errorMessage")
            if message:
                return str(message)
        except (ValueError, TypeError):
            return str(cause)
    return str(error.get("Error", "Ingestion failed for an unknown reason."))


def mark_failed_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    project_id, document_id = event["projectId"], event["documentId"]
    logger.append_keys(projectId=project_id, documentId=document_id)

    repo = deps.get_repo()
    events = deps.get_events()

    status_detail = _status_detail_from_error(event.get("error"))
    logger.warning("ingestion failed", statusDetail=status_detail)

    repo.update_document(document_id, status="FAILED", status_detail=status_detail)
    repo.update_document_ingestion(document_id, finished_at=now_iso())

    events.publish(
        _project_channel(project_id),
        "document.failed",
        {"documentId": document_id, "statusDetail": status_detail},
        seq=0,
    )
    return {"status": "FAILED", "statusDetail": status_detail}
