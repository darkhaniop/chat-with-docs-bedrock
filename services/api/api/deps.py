"""Boto3 clients constructed once per Lambda execution environment, never inside a handler.
`CWD_DOCUMENTS_BUCKET_NAME` is set by `CwdComputeStack` from the data stack's real bucket
resource.
"""

from __future__ import annotations

import os
from functools import lru_cache

import boto3

from common.config import get_settings
from common.repo import Repo
from common.storage import DocumentsStore


@lru_cache(maxsize=1)
def get_repo() -> Repo:
    settings = get_settings()
    client = boto3.client("dynamodb", region_name=settings.aws_region)
    return Repo(client, table_name=settings.table_name)


@lru_cache(maxsize=1)
def get_store() -> DocumentsStore:
    settings = get_settings()
    client = boto3.client("s3", region_name=settings.aws_region)
    bucket = os.environ["CWD_DOCUMENTS_BUCKET_NAME"]
    return DocumentsStore(client, settings, bucket_name=bucket)
