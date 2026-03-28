from __future__ import annotations

import boto3

from api import deps
from api.sweeper import lambda_handler
from common.config import get_settings

_OLD_TIMESTAMP = {"S": "2000-01-01T00:00:00Z"}


def _create_pending_document(project_id: str, filename: str) -> str:
    document = deps.get_repo().create_document(
        project_id=project_id,
        owner_sub="user-1",
        filename=filename,
        content_type="application/pdf",
        byte_size=10,
        kind="pdf",
    )
    return document.document_id


def _backdate_created_at(*keys: dict[str, dict[str, str]]) -> None:
    # Bypasses repo, which always stamps "now", so the sweeper's staleness window has
    # something to actually catch.
    settings = get_settings()
    client = boto3.client("dynamodb", region_name=settings.aws_region)
    for key in keys:
        client.update_item(
            TableName=settings.table_name,
            Key=key,
            UpdateExpression="SET createdAt = :old",
            ExpressionAttributeValues={":old": _OLD_TIMESTAMP},
        )


def test_sweeps_stale_pending_documents_but_not_recent_ones() -> None:
    repo = deps.get_repo()
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    stale_id = _create_pending_document(project.project_id, "stale.pdf")
    _backdate_created_at(
        {"pk": {"S": f"DOC#{stale_id}"}, "sk": {"S": "META"}},
        {"pk": {"S": f"PROJECT#{project.project_id}"}, "sk": {"S": f"DOC#{stale_id}"}},
    )
    recent_id = _create_pending_document(project.project_id, "recent.pdf")

    result = lambda_handler({}, context=None)  # type: ignore[arg-type]

    assert result["sweptCount"] == 1
    assert repo.get_document(stale_id) is None
    assert repo.get_document(recent_id) is not None


def test_does_not_sweep_documents_that_reached_a_terminal_status() -> None:
    repo = deps.get_repo()
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    document_id = _create_pending_document(project.project_id, "ready.pdf")
    repo.update_document(document_id, status="READY")
    _backdate_created_at({"pk": {"S": f"DOC#{document_id}"}, "sk": {"S": "META"}})

    result = lambda_handler({}, context=None)  # type: ignore[arg-type]

    assert result["sweptCount"] == 0
    assert repo.get_document(document_id) is not None
