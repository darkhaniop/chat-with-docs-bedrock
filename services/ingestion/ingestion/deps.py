"""Boto3 clients constructed once per Lambda execution environment, never inside a handler.
Mirrors `services/api/api/deps.py`'s pattern.
"""

from __future__ import annotations

import os
from functools import lru_cache

import boto3

from common.config import get_settings
from common.events import AppSyncEventsPublisher, EventsPublisher
from common.ocr import Textract
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


@lru_cache(maxsize=1)
def get_textract() -> Textract:
    settings = get_settings()
    client = boto3.client("textract", region_name=settings.aws_region)
    return Textract(client)


@lru_cache(maxsize=1)
def get_events() -> EventsPublisher:
    settings = get_settings()
    return AppSyncEventsPublisher(domain=settings.events_http_domain, region=settings.aws_region)
