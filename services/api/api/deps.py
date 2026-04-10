"""Boto3 clients constructed once per Lambda execution environment, never inside a handler.
`CWD_DOCUMENTS_BUCKET_NAME` is set by `CwdComputeStack` from the data stack's real bucket
resource.
"""

from __future__ import annotations

import os
from functools import lru_cache

import boto3

from common.answer_queue import AnswerQueueProtocol, SqsAnswerQueue
from common.config import get_settings
from common.events import AppSyncEventsPublisher, EventsPublisher
from common.repo import Repo
from common.storage import DocumentsStore
from common.vectors import VectorIndex, VectorIndexProtocol
from common.workflow import Workflow


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
def get_workflow() -> Workflow:
    settings = get_settings()
    client = boto3.client("stepfunctions", region_name=settings.aws_region)
    state_machine_arn = os.environ["CWD_INGESTION_STATE_MACHINE_ARN"]
    return Workflow(client, state_machine_arn=state_machine_arn)


@lru_cache(maxsize=1)
def get_vector_index() -> VectorIndexProtocol:
    settings = get_settings()
    client = boto3.client("s3vectors", region_name=settings.aws_region)
    bucket = os.environ["CWD_VECTOR_BUCKET_NAME"]
    return VectorIndex(settings, client, vector_bucket_name=bucket)


@lru_cache(maxsize=1)
def get_answer_queue() -> AnswerQueueProtocol:
    settings = get_settings()
    client = boto3.client("sqs", region_name=settings.aws_region)
    queue_url = os.environ["CWD_ANSWER_QUEUE_URL"]
    return SqsAnswerQueue(client, queue_url=queue_url)


@lru_cache(maxsize=1)
def get_events() -> EventsPublisher:
    settings = get_settings()
    return AppSyncEventsPublisher(domain=settings.events_http_domain, region=settings.aws_region)
