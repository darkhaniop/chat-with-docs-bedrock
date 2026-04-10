"""Exponential-backoff retry for transient Bedrock throttling
(docs/04-retrieval-and-citations.md#failure-behaviour: "Bedrock throttles -> Exponential backoff
inside the Lambda").

`services/ingestion/ingestion/embed.py` and `ingestion/ocr.py` each hand-rolled an identical
`ThrottlingException`-only retry loop before this module existed (Phase 3/4). This is that same
policy, promoted to `common` now that `services/answering` needs a third copy — see
docs/10-roadmap.md's Phase 6 entry.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

from botocore.exceptions import ClientError

T = TypeVar("T")

_DEFAULT_RETRYABLE_CODES = frozenset({"ThrottlingException"})


def with_retry(
    call: Callable[[], T],
    *,
    max_attempts: int,
    base_delay_seconds: float,
    retryable_codes: frozenset[str] = _DEFAULT_RETRYABLE_CODES,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Retries only `retryable_codes` — any other `ClientError` (a real request problem, an auth
    failure) is not transient and should fail immediately rather than burn attempts on something
    retrying can't fix. `max_attempts` counts the original call, so `3` means "retry twice"."""
    attempt = 0
    while True:
        try:
            return call()
        except ClientError as exc:
            attempt += 1
            code = exc.response.get("Error", {}).get("Code")
            if code not in retryable_codes or attempt >= max_attempts:
                raise
            delay = base_delay_seconds * (2 ** (attempt - 1))
            sleep(delay + random.uniform(0, delay * 0.25))  # noqa: S311 — jitter, not security
