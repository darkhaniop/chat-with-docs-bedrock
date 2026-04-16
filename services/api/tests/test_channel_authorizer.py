"""`api.channel_authorizer` — the AppSync Events `onSubscribe` direct-Lambda authorizer
(docs/07-security.md#channel-authorization). Unit-level: exercises the pure decision logic
against a moto-backed `Repo`, independent of a real AppSync invocation (that's
`test_channel_authz.py`'s integration-tier job, docs/08-testing.md)."""

from __future__ import annotations

from typing import Any

from api import deps
from api.channel_authorizer import lambda_handler


def _event(*, segments: list[str], sub: str | None) -> dict[str, Any]:
    identity: Any = {"sub": sub} if sub is not None else "None"
    return {
        "identity": identity,
        "info": {"channel": {"segments": segments, "path": "/" + "/".join(segments)}},
    }


def test_allows_the_project_owner() -> None:
    repo = deps.get_repo()
    project = repo.create_project(owner_sub="user-1", name="p", description="")

    result = lambda_handler(
        _event(segments=["projects", project.project_id], sub="user-1"),
        context=None,  # type: ignore[arg-type]
    )
    assert result is None


def test_denies_a_non_owner_for_a_project_channel() -> None:
    repo = deps.get_repo()
    project = repo.create_project(owner_sub="user-1", name="p", description="")

    result = lambda_handler(
        _event(segments=["projects", project.project_id], sub="user-2"),
        context=None,  # type: ignore[arg-type]
    )
    assert result is not None
    assert "error" in result


def test_allows_the_conversation_owner() -> None:
    repo = deps.get_repo()
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    conversation = repo.create_conversation(
        project_id=project.project_id, owner_sub="user-1", title="", pinned_document_ids=[]
    )

    result = lambda_handler(
        _event(segments=["conversations", conversation.conversation_id], sub="user-1"),
        context=None,  # type: ignore[arg-type]
    )
    assert result is None


def test_denies_a_non_owner_for_a_conversation_channel() -> None:
    repo = deps.get_repo()
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    conversation = repo.create_conversation(
        project_id=project.project_id, owner_sub="user-1", title="", pinned_document_ids=[]
    )

    result = lambda_handler(
        _event(segments=["conversations", conversation.conversation_id], sub="user-2"),
        context=None,  # type: ignore[arg-type]
    )
    assert result is not None


def test_denies_when_the_resource_does_not_exist() -> None:
    result = lambda_handler(
        _event(segments=["projects", "nonexistent"], sub="user-1"),
        context=None,  # type: ignore[arg-type]
    )
    assert result is not None


def test_denies_an_unknown_namespace() -> None:
    result = lambda_handler(
        _event(segments=["something-else", "id"], sub="user-1"),
        context=None,  # type: ignore[arg-type]
    )
    assert result is not None


def test_denies_a_malformed_channel_path() -> None:
    result = lambda_handler(
        _event(segments=["projects"], sub="user-1"),
        context=None,  # type: ignore[arg-type]
    )
    assert result is not None


def test_denies_missing_identity() -> None:
    result = lambda_handler(
        _event(segments=["projects", "some-id"], sub=None),
        context=None,  # type: ignore[arg-type]
    )
    assert result is not None


def test_allows_via_claims_sub_fallback() -> None:
    """Defensive fallback per this module's own `> Verify` note — if AppSync's real Cognito
    identity shape nests `sub` under `claims` instead of top-level, this must still work."""
    repo = deps.get_repo()
    project = repo.create_project(owner_sub="user-1", name="p", description="")
    event = {
        "identity": {"claims": {"sub": "user-1"}},
        "info": {"channel": {"segments": ["projects", project.project_id]}},
    }

    result = lambda_handler(event, context=None)  # type: ignore[arg-type]
    assert result is None
