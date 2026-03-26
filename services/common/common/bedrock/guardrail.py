"""Bedrock Guardrails via the standalone `ApplyGuardrail` API (docs/07 #bedrock-guardrails).

The Messages API path has no inline guardrail parameter, so input and output are each checked
with a separate `apply_guardrail` call. Confirmed live (`tests/contract/smoke_guardrail.py`):
a `PROMPT_ATTACK` content filter must have `outputStrength="NONE"` (`ValidationException`
otherwise — Bedrock only scores prompt-attack on input). `apply_guardrail` with
`guardrailVersion="DRAFT"` works without publishing a numbered version, which is enough for
this smoke test; a deployed guardrail uses a published version per docs/09-operations.md.

Guardrail failures fail **open** (docs/07 — availability preferred over filtering for an
internal audience): callers should catch adapter exceptions and proceed, not block the turn.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from common.config import Settings


@dataclass(frozen=True)
class GuardrailResult:
    blocked: bool
    raw: dict[str, Any]


class Guardrail:
    def __init__(self, settings: Settings, client: Any) -> None:
        self._settings = settings
        self._client = client

    def apply(self, text: str, *, source: Literal["INPUT", "OUTPUT"]) -> GuardrailResult:
        if self._settings.guardrail_id is None:
            return GuardrailResult(blocked=False, raw={})
        response = self._client.apply_guardrail(
            guardrailIdentifier=self._settings.guardrail_id,
            guardrailVersion=self._settings.guardrail_version,
            source=source,
            content=[{"text": {"text": text}}],
        )
        blocked = response.get("action") == "GUARDRAIL_INTERVENED"
        return GuardrailResult(blocked=blocked, raw=response)
