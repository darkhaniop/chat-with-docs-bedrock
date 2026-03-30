"""S3 adapter for the documents bucket (docs/02-data-model.md#s3-layout).

Every key this module touches lives under `raw/` or `pages/` — the prefixes the `api` role is
scoped to (docs/07-security.md#iam). `artifacts/` is never presigned; only ingestion writes and
reads it.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from common.config import Settings


def source_key(project_id: str, document_id: str, extension: str) -> str:
    return f"raw/{project_id}/{document_id}/source.{extension}"


def render_key(project_id: str, document_id: str, page_number: int) -> str:
    return f"pages/{project_id}/{document_id}/{page_number:04d}.png"


class DocumentsStore:
    def __init__(self, client: Any, settings: Settings, *, bucket_name: str) -> None:
        self._client = client
        self._settings = settings
        self._bucket = bucket_name

    def presign_put(self, key: str, *, content_type: str) -> str:
        """Content-Type is a signed parameter — the client's PUT must send exactly this header
        or the signature fails to validate, so this is enough to stop a client uploading a
        different content type than it declared. The declared byte size is capped at request
        validation time (`max_document_bytes`), not re-enforced by the URL itself — S3
        presigned PUT URLs don't support a size *range* condition the way a presigned POST
        policy does, and the true size is re-checked at Probe once bytes exist
        (docs/05-api-contracts.md#documents)."""
        url: str = self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=self._settings.presigned_url_ttl_seconds,
            HttpMethod="PUT",
        )
        return url

    def presign_get(self, key: str) -> str:
        url: str = self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=self._settings.presigned_url_ttl_seconds,
        )
        return url

    def object_exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def delete_prefix(self, prefix: str) -> None:
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if keys:
                self._client.delete_objects(Bucket=self._bucket, Delete={"Objects": keys})
