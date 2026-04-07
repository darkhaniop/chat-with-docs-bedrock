"""Query rewriting (docs/04-retrieval-and-citations.md#1-query-rewriting): a cheap Haiku 4.5
call turns a follow-up like "what about the second one?" into a standalone search query, using
the recent conversation history to resolve pronouns and elliptical references.
"""

from __future__ import annotations

from common.bedrock.messages import BedrockMessages
from common.config import Settings

# (role, text) pairs, oldest first — not `common.models.Message`, so the answering Lambda's
# invoke payload (JSON across the api -> answering boundary) never has to fabricate a full
# `Message` model for history it didn't itself persist.
HistoryTurn = tuple[str, str]

REWRITE_SYSTEM = """\
You rewrite the latest user message into a single standalone search query for a document \
retrieval system.

Rules:
- Resolve every pronoun and elliptical reference using the conversation history.
- Preserve the user's domain terms, names, numbers, and units verbatim.
- Output only the query. No preamble, no quotes, no explanation.
- If the latest message is already standalone, output it unchanged.
"""


def _history_text(history: list[HistoryTurn], *, max_turns: int, max_chars: int) -> str:
    recent = history[-max_turns:]
    text = "\n".join(f"{role}: {text}" for role, text in recent)
    return text[-max_chars:] if len(text) > max_chars else text


def _response_text(response: dict[str, object]) -> str:
    content = response.get("content")
    if not isinstance(content, list):
        return ""
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                return text
    return ""


def rewrite_query(
    bedrock: BedrockMessages,
    settings: Settings,
    *,
    history: list[HistoryTurn],
    latest_message: str,
) -> str:
    """Returns a standalone query, falling back to `latest_message` unchanged on any failure —
    timeout, guardrail, empty output, or a malformed response — because "a degraded query beats
    a failed turn" (docs/04). The first message in a conversation (no history yet) skips the
    call entirely: there is nothing for a pronoun to resolve against."""
    if not history:
        return latest_message

    history_text = _history_text(
        history,
        max_turns=settings.rewrite_history_turns,
        max_chars=settings.rewrite_history_max_chars,
    )
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Conversation history:\n{history_text}\n\nLatest message: {latest_message}"
                    ),
                }
            ],
        }
    ]
    try:
        response = bedrock.create(
            model_id=settings.haiku_model_id,
            system=REWRITE_SYSTEM,
            messages=messages,
            max_tokens=settings.rewrite_max_tokens,
            thinking={"type": "disabled"},
            effort=settings.rewrite_effort,
        )
    except Exception:
        return latest_message

    text = _response_text(response).strip()
    return text if text else latest_message
