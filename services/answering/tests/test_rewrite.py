"""`answering.rewrite.rewrite_query` (docs/04-retrieval-and-citations.md #1-query-rewriting):
prompt construction and the "fall back to the raw message on any failure" contract.
"""

from __future__ import annotations

from typing import Any

from answering.rewrite import rewrite_query
from common.config import get_settings


class _StubBedrock:
    def __init__(self, response: dict[str, Any] | None = None, *, raises: bool = False) -> None:
        self._response = response
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._raises:
            raise RuntimeError("boom")
        assert self._response is not None
        return self._response


def _text_response(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def test_first_message_in_a_conversation_skips_the_rewrite_entirely() -> None:
    bedrock = _StubBedrock()
    settings = get_settings()

    result = rewrite_query(bedrock, settings, history=[], latest_message="What was Q3 uptime?")

    assert result == "What was Q3 uptime?"
    assert bedrock.calls == []


def test_rewrite_returns_the_models_text_when_history_exists() -> None:
    bedrock = _StubBedrock(_text_response("What was Q3 uptime at the Ashford facility?"))
    settings = get_settings()
    history = [
        ("user", "Tell me about the Ashford facility"),
        ("assistant", "Sure, what about it?"),
    ]

    result = rewrite_query(bedrock, settings, history=history, latest_message="What was Q3 uptime?")

    assert result == "What was Q3 uptime at the Ashford facility?"
    [call] = bedrock.calls
    assert call["model_id"] == settings.haiku_model_id
    assert call["thinking"] == {"type": "disabled"}
    assert "Ashford facility" in call["messages"][0]["content"][0]["text"]


def test_rewrite_falls_back_to_the_raw_message_on_bedrock_failure() -> None:
    bedrock = _StubBedrock(raises=True)
    settings = get_settings()
    history = [("user", "hi"), ("assistant", "hello")]

    result = rewrite_query(bedrock, settings, history=history, latest_message="what about it?")

    assert result == "what about it?"


def test_rewrite_falls_back_to_the_raw_message_on_an_empty_response() -> None:
    bedrock = _StubBedrock(_text_response("   "))
    settings = get_settings()
    history = [("user", "hi"), ("assistant", "hello")]

    result = rewrite_query(bedrock, settings, history=history, latest_message="what about it?")

    assert result == "what about it?"


def test_rewrite_falls_back_when_the_response_has_no_text_block() -> None:
    bedrock = _StubBedrock({"content": []})
    settings = get_settings()
    history = [("user", "hi"), ("assistant", "hello")]

    result = rewrite_query(bedrock, settings, history=history, latest_message="what about it?")

    assert result == "what about it?"


def test_history_text_truncates_to_the_configured_turn_and_char_limits() -> None:
    bedrock = _StubBedrock(_text_response("standalone query"))
    settings = get_settings().model_copy(
        update={"rewrite_history_turns": 2, "rewrite_history_max_chars": 40}
    )
    history = [("user", "a" * 100), ("assistant", "b" * 100), ("user", "recent question")]

    rewrite_query(bedrock, settings, history=history, latest_message="follow-up")

    [call] = bedrock.calls
    prompt_text = call["messages"][0]["content"][0]["text"]
    assert "a" * 100 not in prompt_text
    assert "recent question" in prompt_text
