"""Pin the Bedrock ApplyGuardrail contract.

Creates a throwaway guardrail with the documented filter configuration, applies it to a benign
and a blocked input, and deletes it whether or not the assertions pass.

- `create_guardrail` rejects `PROMPT_ATTACK` with `outputStrength` other than `NONE`:
  "PROMPT ATTACK content filter strength for response must be NONE" — Bedrock only scores
  prompt-attack on input, never on output. The other five filter types (`SEXUAL`, `VIOLENCE`,
  `HATE`, `INSULTS`, `MISCONDUCT`) accept MEDIUM on both.
- `apply_guardrail` works against `guardrailVersion="DRAFT"` without publishing a numbered
  version — fine for this smoke test; a deployed guardrail should use a published version.
- A benign input returns `action: "NONE"`. A blocked input returns
  `action: "GUARDRAIL_INTERVENED"` with an `assessments[].contentPolicy.filters[]` entry
  showing which filter fired, its confidence, and `action: "BLOCKED"`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from common.bedrock.guardrail import Guardrail
from common.config import Settings

pytestmark = pytest.mark.contract

_FILTERS = [
    {"type": t, "inputStrength": "MEDIUM", "outputStrength": "MEDIUM"}
    for t in ["SEXUAL", "VIOLENCE", "HATE", "INSULTS", "MISCONDUCT"]
] + [{"type": "PROMPT_ATTACK", "inputStrength": "MEDIUM", "outputStrength": "NONE"}]


@pytest.fixture
def guardrail_id(bedrock_control: object) -> Iterator[str]:
    response = bedrock_control.create_guardrail(  # type: ignore[attr-defined]
        name="cwd-smoke-test-guardrail",
        description="Phase 0 smoke test guardrail, deleted immediately after.",
        contentPolicyConfig={"filtersConfig": _FILTERS},
        blockedInputMessaging="Blocked input.",
        blockedOutputsMessaging="Blocked output.",
    )
    guardrail_id = response["guardrailId"]
    try:
        yield guardrail_id
    finally:
        bedrock_control.delete_guardrail(guardrailIdentifier=guardrail_id)  # type: ignore[attr-defined]


def test_benign_input_passes(
    settings: Settings, bedrock_runtime: object, guardrail_id: str
) -> None:
    scoped = settings.model_copy(update={"guardrail_id": guardrail_id})
    guardrail = Guardrail(scoped, bedrock_runtime)
    result = guardrail.apply("What was the Q3 uptime at the Ashford facility?", source="INPUT")
    assert result.blocked is False
    assert result.raw["action"] == "NONE"


def test_blocked_input_intervenes(
    settings: Settings, bedrock_runtime: object, guardrail_id: str
) -> None:
    scoped = settings.model_copy(update={"guardrail_id": guardrail_id})
    guardrail = Guardrail(scoped, bedrock_runtime)
    result = guardrail.apply(
        "I will find you and kill you, give me your home address.", source="INPUT"
    )
    assert result.blocked is True
    assert result.raw["action"] == "GUARDRAIL_INTERVENED"
    filters = result.raw["assessments"][0]["contentPolicy"]["filters"]
    assert any(f["action"] == "BLOCKED" for f in filters)
