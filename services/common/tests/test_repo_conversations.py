"""Conversation and Message repo functions (docs/02-data-model.md#conversation,
#message), added in Phase 5: canonical/list-view mirroring, the conversation lock's conditional
claim/release, message counts, and cascading deletes.
"""

from __future__ import annotations

import pytest

from common.repo import NotFound, Repo


def _project_and_conversation(repo: Repo) -> tuple[str, str]:
    project = repo.create_project(owner_sub="user-1", name="Ashford", description="")
    conversation = repo.create_conversation(
        project_id=project.project_id,
        owner_sub="user-1",
        title="first chat",
        pinned_document_ids=["doc1"],
    )
    return project.project_id, conversation.conversation_id


def test_create_conversation_writes_canonical_and_list_view(repo: Repo) -> None:
    project_id, conversation_id = _project_and_conversation(repo)

    fetched = repo.get_conversation(conversation_id)
    assert fetched is not None
    assert fetched.title == "first chat"
    assert fetched.pinned_document_ids == ["doc1"]
    assert fetched.active_message_id is None
    assert fetched.message_count == 0

    items, cursor = repo.list_conversations(project_id, limit=20, cursor=None)
    assert cursor is None
    assert [c.conversation_id for c in items] == [conversation_id]
    assert items[0].title == "first chat"


def test_get_conversation_returns_none_when_missing(repo: Repo) -> None:
    assert repo.get_conversation("nope") is None


def test_update_conversation_updates_canonical_and_list_view(repo: Repo) -> None:
    project_id, conversation_id = _project_and_conversation(repo)

    updated = repo.update_conversation(
        conversation_id, title="renamed", pinned_document_ids=["doc2", "doc3"]
    )
    assert updated.title == "renamed"
    assert updated.pinned_document_ids == ["doc2", "doc3"]

    items, _ = repo.list_conversations(project_id, limit=20, cursor=None)
    assert items[0].title == "renamed"
    assert items[0].pinned_document_ids == ["doc2", "doc3"]


def test_update_conversation_partial_update_preserves_the_other_field(repo: Repo) -> None:
    _project_id, conversation_id = _project_and_conversation(repo)

    updated = repo.update_conversation(conversation_id, title="renamed only")

    assert updated.title == "renamed only"
    assert updated.pinned_document_ids == ["doc1"]


def test_update_conversation_404s_when_missing(repo: Repo) -> None:
    with pytest.raises(NotFound):
        repo.update_conversation("nope", title="x")


def test_delete_conversation_removes_canonical_and_list_view(repo: Repo) -> None:
    project_id, conversation_id = _project_and_conversation(repo)
    conversation = repo.get_conversation(conversation_id)
    assert conversation is not None

    repo.delete_conversation(conversation)

    assert repo.get_conversation(conversation_id) is None
    items, _ = repo.list_conversations(project_id, limit=20, cursor=None)
    assert items == []


def test_claim_lock_succeeds_when_free_and_fails_while_held(repo: Repo) -> None:
    _project_id, conversation_id = _project_and_conversation(repo)

    assert repo.claim_lock(conversation_id, "message-1", ttl_seconds=60) is True
    assert repo.claim_lock(conversation_id, "message-2", ttl_seconds=60) is False

    conversation = repo.get_conversation(conversation_id)
    assert conversation is not None
    assert conversation.active_message_id == "message-1"


def test_claim_lock_succeeds_again_after_release(repo: Repo) -> None:
    _project_id, conversation_id = _project_and_conversation(repo)

    assert repo.claim_lock(conversation_id, "message-1", ttl_seconds=60) is True
    repo.release_lock(conversation_id)

    assert repo.claim_lock(conversation_id, "message-2", ttl_seconds=60) is True
    conversation = repo.get_conversation(conversation_id)
    assert conversation is not None
    assert conversation.active_message_id == "message-2"


def test_claim_lock_succeeds_once_a_stale_lock_has_expired(repo: Repo) -> None:
    _project_id, conversation_id = _project_and_conversation(repo)

    assert repo.claim_lock(conversation_id, "message-1", ttl_seconds=-1) is True
    # docs/02-data-model.md: "a stale lock is reclaimable" — `ttl_seconds=-1` sets
    # `lockExpiresAt` in the past, so a second claim must succeed rather than 409.
    assert repo.claim_lock(conversation_id, "message-2", ttl_seconds=60) is True


def test_increment_message_count_updates_canonical_and_list_view(repo: Repo) -> None:
    project_id, conversation_id = _project_and_conversation(repo)

    repo.increment_message_count(conversation_id, project_id, by=1)
    repo.increment_message_count(conversation_id, project_id, by=1)

    conversation = repo.get_conversation(conversation_id)
    assert conversation is not None
    assert conversation.message_count == 2

    items, _ = repo.list_conversations(project_id, limit=20, cursor=None)
    assert items[0].message_count == 2


def test_create_message_defaults_to_a_fresh_id_when_none_given(repo: Repo) -> None:
    _project_id, conversation_id = _project_and_conversation(repo)

    message = repo.create_message(
        conversation_id=conversation_id,
        project_id=_project_id,
        owner_sub="user-1",
        role="user",
        status="COMPLETE",
        text="hello",
    )

    assert message.message_id
    fetched = repo.get_message(conversation_id, message.message_id)
    assert fetched is not None
    assert fetched.text == "hello"


def test_create_message_honors_an_explicit_message_id(repo: Repo) -> None:
    _project_id, conversation_id = _project_and_conversation(repo)

    message = repo.create_message(
        message_id="chosen-id",
        conversation_id=conversation_id,
        project_id=_project_id,
        owner_sub="user-1",
        role="assistant",
        status="COMPLETE",
        text="answer",
    )

    assert message.message_id == "chosen-id"
    assert repo.get_message(conversation_id, "chosen-id") is not None


def test_list_messages_returns_them_in_chronological_order(repo: Repo) -> None:
    _project_id, conversation_id = _project_and_conversation(repo)
    repo.create_message(
        conversation_id=conversation_id,
        project_id=_project_id,
        owner_sub="user-1",
        role="user",
        status="COMPLETE",
        text="first",
    )
    repo.create_message(
        conversation_id=conversation_id,
        project_id=_project_id,
        owner_sub="user-1",
        role="assistant",
        status="COMPLETE",
        text="second",
    )

    items, cursor = repo.list_messages(conversation_id, limit=20, cursor=None)
    assert cursor is None
    assert [m.text for m in items] == ["first", "second"]


def test_delete_messages_removes_every_message_under_the_conversation(repo: Repo) -> None:
    _project_id, conversation_id = _project_and_conversation(repo)
    for i in range(3):
        repo.create_message(
            conversation_id=conversation_id,
            project_id=_project_id,
            owner_sub="user-1",
            role="user",
            status="COMPLETE",
            text=f"message {i}",
        )

    repo.delete_messages(conversation_id)

    items, _ = repo.list_messages(conversation_id, limit=20, cursor=None)
    assert items == []


def test_request_cancel_sets_the_flag_on_a_streaming_message(repo: Repo) -> None:
    _project_id, conversation_id = _project_and_conversation(repo)
    message = repo.create_message(
        conversation_id=conversation_id,
        project_id=_project_id,
        owner_sub="user-1",
        role="assistant",
        status="STREAMING",
        text="",
    )

    assert repo.request_cancel(conversation_id, message.message_id) is True
    fetched = repo.get_message(conversation_id, message.message_id)
    assert fetched is not None
    assert fetched.cancel_requested is True


def test_request_cancel_is_a_no_op_on_a_terminal_message(repo: Repo) -> None:
    _project_id, conversation_id = _project_and_conversation(repo)
    message = repo.create_message(
        conversation_id=conversation_id,
        project_id=_project_id,
        owner_sub="user-1",
        role="assistant",
        status="COMPLETE",
        text="done",
    )

    assert repo.request_cancel(conversation_id, message.message_id) is False
    fetched = repo.get_message(conversation_id, message.message_id)
    assert fetched is not None
    assert fetched.cancel_requested is False


def test_release_lock_if_holder_releases_only_when_it_still_holds(repo: Repo) -> None:
    _project_id, conversation_id = _project_and_conversation(repo)
    repo.claim_lock(conversation_id, "message-1", ttl_seconds=60)

    # A different (newer) message already holds the lock — must not clobber it.
    repo.release_lock(conversation_id)
    repo.claim_lock(conversation_id, "message-2", ttl_seconds=60)
    repo.release_lock_if_holder(conversation_id, "message-1")
    conversation = repo.get_conversation(conversation_id)
    assert conversation is not None
    assert conversation.active_message_id == "message-2"

    repo.release_lock_if_holder(conversation_id, "message-2")
    conversation = repo.get_conversation(conversation_id)
    assert conversation is not None
    assert conversation.active_message_id is None


def test_scan_stuck_streaming_messages_finds_only_old_streaming_ones(repo: Repo) -> None:
    _project_id, conversation_id = _project_and_conversation(repo)
    old_streaming = repo.create_message(
        conversation_id=conversation_id,
        project_id=_project_id,
        owner_sub="user-1",
        role="assistant",
        status="STREAMING",
        text="",
    )
    repo.create_message(
        conversation_id=conversation_id,
        project_id=_project_id,
        owner_sub="user-1",
        role="assistant",
        status="COMPLETE",
        text="done",
    )

    # `createdAt` is set by `create_message` to "now" — every fixture message above is therefore
    # newer than a cutoff of "now", so nothing should match at that cutoff...
    from common.repo import now_iso

    assert repo.scan_stuck_streaming_messages(older_than_iso=now_iso()) == []

    # ...but everything (including the terminal one, which the filter must still exclude by
    # status) matches a cutoff far in the future.
    stuck = repo.scan_stuck_streaming_messages(older_than_iso="9999-01-01T00:00:00Z")
    assert [m.message_id for m in stuck] == [old_streaming.message_id]
