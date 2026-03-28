"""Daily scheduled Lambda (docs/02-data-model.md#s3-layout): deletes `PENDING` documents whose
client never called `:ingest`, along with their `raw/` S3 object. Runs as a second CMD on the
same `api` Docker image (docs/01-architecture.md#compute-packaging) — it shares `cwd-api`'s
dependency set and has nothing large enough (PyMuPDF, Pillow, …) to justify its own image.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

from api import deps, documents
from common.config import get_settings
from common.repo import now_iso

logger = Logger(service="document-sweeper")


def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(hours=settings.orphan_upload_staleness_hours)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    repo = deps.get_repo()
    store = deps.get_store()
    stale = repo.scan_stale_pending_documents(older_than_iso=cutoff_iso)

    for document in stale:
        documents.delete_storage(store, document)
        repo.delete_document(document)

    logger.info("orphan sweep complete", sweptCount=len(stale), cutoff=cutoff_iso, now=now_iso())
    return {"sweptCount": len(stale)}
