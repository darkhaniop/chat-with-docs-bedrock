"""Confirm the pinned model id strings actually invoke in us-east-1.

Both the `us.` and `global.` cross-region inference-profile prefixes work for Sonnet 4.6 and
Haiku 4.5.Nova has no inference profile — the base model id is invoked directly and works.
"""

from __future__ import annotations

import json

import pytest

from common.bedrock.messages import BedrockMessages
from common.config import Settings

pytestmark = pytest.mark.contract


def test_sonnet_invokes(settings: Settings, bedrock_runtime: object) -> None:
    client = BedrockMessages(settings, bedrock_runtime)
    response = client.create(
        model_id=settings.sonnet_model_id,
        system="Reply with one word.",
        messages=[{"role": "user", "content": [{"type": "text", "text": "Say hi."}]}],
        max_tokens=20,
    )
    assert response["content"][0]["text"]


def test_haiku_invokes(settings: Settings, bedrock_runtime: object) -> None:
    client = BedrockMessages(settings, bedrock_runtime)
    response = client.create(
        model_id=settings.haiku_model_id,
        system="Reply with one word.",
        messages=[{"role": "user", "content": [{"type": "text", "text": "Say hi."}]}],
        max_tokens=20,
    )
    assert response["content"][0]["text"]


def test_bare_model_id_rejected_without_inference_profile(bedrock_runtime: object) -> None:
    """Documents why Settings uses the `us.` prefix rather than the bare model id."""
    with pytest.raises(Exception) as exc_info:
        bedrock_runtime.invoke_model(  # type: ignore[attr-defined]
            modelId="anthropic.claude-sonnet-4-6",
            body=json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 10,
                    "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
                }
            ),
        )
    assert "inference profile" in str(exc_info.value)


def test_nova_base_model_id_invokes(settings: Settings, bedrock_runtime: object) -> None:
    response = bedrock_runtime.invoke_model(  # type: ignore[attr-defined]
        modelId=settings.nova_model_id,
        body=json.dumps(
            {
                "taskType": "SINGLE_EMBEDDING",
                "singleEmbeddingParams": {
                    "embeddingDimension": settings.embed_dim,
                    "embeddingPurpose": settings.nova_embed_purpose_index,
                    "text": {"truncationMode": "END", "value": "smoke test"},
                },
            }
        ),
    )
    payload = json.loads(response["body"].read())
    assert len(payload["embeddings"][0]["embedding"]) == settings.embed_dim
