"""Fixtures for the Phase 0 live contract smoke tests.

These need real AWS credentials (e.g. `AWS_PROFILE=cdk-dev`) and hit real Bedrock/S3
Vectors/Bedrock-control APIs. They are excluded from the default `pytest` run by
`-m "not integration and not contract"` in the root pyproject; run them explicitly with
`uv run pytest -m contract`.
"""

from __future__ import annotations

from collections.abc import Iterator

import boto3
import pytest

from common.config import Settings, get_settings


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


@pytest.fixture(scope="session")
def bedrock_runtime(settings: Settings) -> Iterator[object]:
    yield boto3.client("bedrock-runtime", region_name=settings.aws_region)


@pytest.fixture(scope="session")
def bedrock_control(settings: Settings) -> Iterator[object]:
    yield boto3.client("bedrock", region_name=settings.aws_region)


@pytest.fixture(scope="session")
def s3vectors(settings: Settings) -> Iterator[object]:
    yield boto3.client("s3vectors", region_name=settings.aws_region)
