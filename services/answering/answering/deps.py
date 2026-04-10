"""Boto3 clients constructed once per Lambda execution environment, never inside a handler.
Mirrors `services/api/api/deps.py`/`services/ingestion/ingestion/deps.py`'s pattern.
"""

from __future__ import annotations

import os
from functools import lru_cache

import boto3

from common.bedrock.embeddings import NovaEmbeddings, NovaEmbeddingsProtocol
from common.bedrock.guardrail import Guardrail
from common.bedrock.messages import BedrockMessages
from common.config import get_settings
from common.events import AppSyncEventsPublisher, EventsPublisher
from common.repo import Repo
from common.storage import DocumentsStore
from common.vectors import VectorIndex, VectorIndexProtocol


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
def get_vector_index() -> VectorIndexProtocol:
    """`answering`'s role only has `s3vectors:QueryVectors` (docs/07-security.md#iam) — it
    reads the project's index, never creates or writes to it."""
    settings = get_settings()
    client = boto3.client("s3vectors", region_name=settings.aws_region)
    bucket = os.environ["CWD_VECTOR_BUCKET_NAME"]
    return VectorIndex(settings, client, vector_bucket_name=bucket)


@lru_cache(maxsize=1)
def get_nova() -> NovaEmbeddingsProtocol:
    settings = get_settings()
    client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    return NovaEmbeddings(settings, client)


@lru_cache(maxsize=1)
def get_bedrock() -> BedrockMessages:
    settings = get_settings()
    client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    return BedrockMessages(settings, client)


@lru_cache(maxsize=1)
def get_guardrail() -> Guardrail:
    settings = get_settings()
    client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    return Guardrail(settings, client)


@lru_cache(maxsize=1)
def get_events() -> EventsPublisher:
    settings = get_settings()
    return AppSyncEventsPublisher(domain=settings.events_http_domain, region=settings.aws_region)
