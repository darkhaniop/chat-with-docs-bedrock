from __future__ import annotations

import json
from typing import Any

from api.handler import lambda_handler

_CLAIMS = {"sub": "user-1"}
_OTHER_CLAIMS = {"sub": "user-2"}


def _event(
    route_key: str,
    *,
    path_parameters: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
    claims: dict[str, Any] | None = _CLAIMS,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_context: dict[str, Any] = {"requestId": "req-1"}
    if claims is not None:
        request_context["authorizer"] = {"jwt": {"claims": claims}}
    event: dict[str, Any] = {"routeKey": route_key, "requestContext": request_context}
    if path_parameters is not None:
        event["pathParameters"] = path_parameters
    if query is not None:
        event["queryStringParameters"] = query
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def _call(event: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    response = lambda_handler(event, context=None)  # type: ignore[arg-type]
    return response["statusCode"], json.loads(response["body"])


def test_health_is_unauthenticated_and_reports_status_ok() -> None:
    status, body = _call(_event("GET /health", claims=None))
    assert status == 200
    assert body["status"] == "ok"


def test_unknown_route_returns_404_error_envelope() -> None:
    status, body = _call(_event("GET /nope"))
    assert status == 404
    assert body["error"]["code"] == "NOT_FOUND"


def _create_project(name: str = "Ashford") -> dict[str, Any]:
    status, body = _call(_event("POST /projects", body={"name": name}))
    assert status == 201
    return body


def test_create_then_get_project() -> None:
    created = _create_project()
    assert created["name"] == "Ashford"
    assert "ownerSub" not in created

    status, body = _call(
        _event(
            "GET /projects/{projectId}",
            path_parameters={"projectId": created["projectId"]},
        )
    )
    assert status == 200
    assert body["projectId"] == created["projectId"]


def test_create_project_rejects_missing_name() -> None:
    status, body = _call(_event("POST /projects", body={}))
    assert status == 400
    assert body["error"]["code"] == "VALIDATION_ERROR"


def test_list_projects_only_shows_the_callers_own() -> None:
    _create_project("mine")
    status, body = _call(_event("POST /projects", claims=_OTHER_CLAIMS, body={"name": "theirs"}))
    assert status == 201

    status, body = _call(_event("GET /projects"))
    assert status == 200
    assert [p["name"] for p in body["items"]] == ["mine"]


def test_patch_project_updates_name() -> None:
    created = _create_project()
    status, body = _call(
        _event(
            "PATCH /projects/{projectId}",
            path_parameters={"projectId": created["projectId"]},
            body={"name": "renamed"},
        )
    )
    assert status == 200
    assert body["name"] == "renamed"


def test_get_project_404s_for_a_different_user() -> None:
    created = _create_project()
    status, body = _call(
        _event(
            "GET /projects/{projectId}",
            path_parameters={"projectId": created["projectId"]},
            claims=_OTHER_CLAIMS,
        )
    )
    assert status == 404
    assert body["error"]["code"] == "NOT_FOUND"


def test_delete_project_removes_it_from_the_list() -> None:
    created = _create_project()
    status, body = _call(
        _event(
            "DELETE /projects/{projectId}",
            path_parameters={"projectId": created["projectId"]},
        )
    )
    assert status == 202
    assert body["status"] == "DELETING"

    status, body = _call(_event("GET /projects"))
    assert body["items"] == []


def _upload_document(project_id: str, filename: str = "report.pdf") -> dict[str, Any]:
    status, body = _call(
        _event(
            "POST /projects/{projectId}/documents",
            path_parameters={"projectId": project_id},
            body={"filename": filename, "contentType": "application/pdf", "byteSize": 1234},
        )
    )
    assert status == 201
    return body


def test_create_document_returns_a_presigned_put() -> None:
    project = _create_project()
    created = _upload_document(project["projectId"])

    assert created["document"]["status"] == "PENDING"
    assert created["document"]["filename"] == "report.pdf"
    assert created["upload"]["method"] == "PUT"
    assert created["upload"]["headers"]["Content-Type"] == "application/pdf"
    assert created["document"]["documentId"] in created["upload"]["url"]


def test_create_document_rejects_unsupported_content_type() -> None:
    project = _create_project()
    status, body = _call(
        _event(
            "POST /projects/{projectId}/documents",
            path_parameters={"projectId": project["projectId"]},
            body={"filename": "a.docx", "contentType": "application/msword", "byteSize": 10},
        )
    )
    assert status == 400
    assert body["error"]["code"] == "UNSUPPORTED_CONTENT_TYPE"


def test_create_document_under_someone_elses_project_is_404() -> None:
    project = _create_project()
    status, body = _call(
        _event(
            "POST /projects/{projectId}/documents",
            path_parameters={"projectId": project["projectId"]},
            claims=_OTHER_CLAIMS,
            body={"filename": "a.pdf", "contentType": "application/pdf", "byteSize": 10},
        )
    )
    assert status == 404


def test_list_documents_reflects_created_document() -> None:
    project = _create_project()
    _upload_document(project["projectId"])

    status, body = _call(
        _event(
            "GET /projects/{projectId}/documents",
            path_parameters={"projectId": project["projectId"]},
        )
    )
    assert status == 200
    assert len(body["items"]) == 1
    assert body["items"][0]["filename"] == "report.pdf"


def test_get_document() -> None:
    project = _create_project()
    document = _upload_document(project["projectId"])["document"]

    status, body = _call(
        _event(
            "GET /projects/{projectId}/documents/{documentId}",
            path_parameters={
                "projectId": project["projectId"],
                "documentId": document["documentId"],
            },
        )
    )
    assert status == 200
    assert body["documentId"] == document["documentId"]


def test_get_document_404s_when_the_project_in_the_path_is_wrong() -> None:
    project_a = _create_project("a")
    project_b = _create_project("b")
    document = _upload_document(project_a["projectId"])["document"]

    status, body = _call(
        _event(
            "GET /projects/{projectId}/documents/{documentId}",
            path_parameters={
                "projectId": project_b["projectId"],
                "documentId": document["documentId"],
            },
        )
    )
    assert status == 404


def test_delete_document_removes_it_and_decrements_project_count() -> None:
    project = _create_project()
    document = _upload_document(project["projectId"])["document"]

    status, body = _call(
        _event(
            "DELETE /projects/{projectId}/documents/{documentId}",
            path_parameters={
                "projectId": project["projectId"],
                "documentId": document["documentId"],
            },
        )
    )
    assert status == 202

    status, body = _call(
        _event(
            "GET /projects/{projectId}",
            path_parameters={"projectId": project["projectId"]},
        )
    )
    assert body["documentCount"] == 0


def test_deleting_a_project_cascades_to_its_documents() -> None:
    project = _create_project()
    _upload_document(project["projectId"], filename="a.pdf")
    _upload_document(project["projectId"], filename="b.pdf")

    status, _ = _call(
        _event(
            "DELETE /projects/{projectId}",
            path_parameters={"projectId": project["projectId"]},
        )
    )
    assert status == 202

    status, body = _call(
        _event(
            "GET /projects/{projectId}/documents/{documentId}",
            path_parameters={"projectId": project["projectId"], "documentId": "anything"},
        )
    )
    assert status == 404


def test_source_url_returns_a_presigned_get() -> None:
    project = _create_project()
    document = _upload_document(project["projectId"])["document"]

    status, body = _call(
        _event(
            "GET /projects/{projectId}/documents/{documentId}/source-url",
            path_parameters={
                "projectId": project["projectId"],
                "documentId": document["documentId"],
            },
        )
    )
    assert status == 200
    assert document["documentId"] in body["url"]
    assert "expiresAt" in body


def test_render_url_rejects_a_non_positive_page_number() -> None:
    project = _create_project()
    document = _upload_document(project["projectId"])["document"]

    status, body = _call(
        _event(
            "GET /projects/{projectId}/documents/{documentId}/pages/{page}/render-url",
            path_parameters={
                "projectId": project["projectId"],
                "documentId": document["documentId"],
                "page": "0",
            },
        )
    )
    assert status == 400


def test_render_url_returns_a_presigned_get_for_a_valid_page() -> None:
    project = _create_project()
    document = _upload_document(project["projectId"])["document"]

    status, body = _call(
        _event(
            "GET /projects/{projectId}/documents/{documentId}/pages/{page}/render-url",
            path_parameters={
                "projectId": project["projectId"],
                "documentId": document["documentId"],
                "page": "1",
            },
        )
    )
    assert status == 200
    assert "expiresAt" in body


def _ingest(project_id: str, document_id: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
    return _call(
        _event(
            "POST /projects/{projectId}/documents/{documentId}/ingest",
            path_parameters={"projectId": project_id, "documentId": document_id},
            **kwargs,
        )
    )


def test_ingest_starts_an_execution_and_flips_status_to_processing() -> None:
    project = _create_project()
    document = _upload_document(project["projectId"])["document"]

    status, body = _ingest(project["projectId"], document["documentId"])

    assert status == 202
    assert body["executionArn"].startswith("arn:aws:states:")

    status, refreshed = _call(
        _event(
            "GET /projects/{projectId}/documents/{documentId}",
            path_parameters={
                "projectId": project["projectId"],
                "documentId": document["documentId"],
            },
        )
    )
    assert refreshed["status"] == "PROCESSING"


def test_ingest_twice_in_a_row_returns_409() -> None:
    project = _create_project()
    document = _upload_document(project["projectId"])["document"]

    first_status, _ = _ingest(project["projectId"], document["documentId"])
    second_status, second_body = _ingest(project["projectId"], document["documentId"])

    assert first_status == 202
    assert second_status == 409
    assert second_body["error"]["code"] == "INGEST_IN_PROGRESS"


def test_ingest_under_someone_elses_document_is_404() -> None:
    project = _create_project()
    document = _upload_document(project["projectId"])["document"]

    status, _ = _ingest(project["projectId"], document["documentId"], claims=_OTHER_CLAIMS)

    assert status == 404


def test_reingesting_a_ready_document_sweeps_prior_pages_chunks_and_chunk_count() -> None:
    from api import deps
    from common.models import Chunk, DocumentIngestion, Page, Sentence

    project = _create_project()
    document = _upload_document(project["projectId"])["document"]
    project_id, document_id = project["projectId"], document["documentId"]

    repo = deps.get_repo()
    repo.put_page(
        Page(
            document_id=document_id,
            page_number=1,
            width=612.0,
            height=792.0,
            text_source="pdf",
            text_density=0.5,
        )
    )
    repo.batch_write_chunks(
        [
            Chunk(
                chunk_id="c1",
                document_id=document_id,
                project_id=project_id,
                page_number=1,
                ordinal=0,
                text="Some chunk text.",
                sentences=[Sentence(i=0, text="Some chunk text.", rects=[(0.0, 0.0, 1.0, 1.0)])],
                token_estimate=5,
                created_at="2026-01-01T00:00:00Z",
            )
        ]
    )
    repo.increment_chunk_count(project_id, by=1)
    repo.update_document(
        document_id,
        status="READY",
        ingestion=DocumentIngestion(chunk_count=1, ocr_pages=0),
    )

    status, body = _ingest(project_id, document_id)

    assert status == 202
    assert repo.get_page(document_id, 1) is None
    assert repo.get_chunk(document_id, "c1") is None
    refreshed_project = repo.get_project(project_id)
    assert refreshed_project is not None
    assert refreshed_project.chunk_count == 0
