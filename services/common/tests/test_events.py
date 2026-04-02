"""AppSync Events publish adapter (docs/05-api-contracts.md#appsync-events)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from common.events import AppSyncEventsPublisher
from common.testing.events import FakeEventsPublisher


def test_publish_is_a_no_op_when_no_domain_is_configured() -> None:
    publisher = AppSyncEventsPublisher(domain=None, region="us-east-1")
    # Must not raise and must not attempt any network call.
    with patch("urllib.request.urlopen") as urlopen:
        publisher.publish("/projects/p1", "document.progress", {"pagesDone": 1}, seq=1)
        urlopen.assert_not_called()


def test_publish_sends_a_signed_post_with_the_documented_envelope() -> None:
    publisher = AppSyncEventsPublisher(
        domain="abc123.appsync-api.us-east-1.amazonaws.com", region="us-east-1"
    )
    fake_credentials = MagicMock()
    fake_credentials.access_key = "AKIA_FAKE"
    fake_credentials.secret_key = "fake_secret"
    fake_credentials.token = None
    publisher._session = MagicMock()
    publisher._session.get_credentials.return_value = fake_credentials

    fake_response = MagicMock()
    fake_response.status = 200
    fake_response.__enter__.return_value = fake_response

    with patch("urllib.request.urlopen", return_value=fake_response) as urlopen:
        publisher.publish("/projects/p1", "document.progress", {"pagesDone": 1}, seq=3)

    urlopen.assert_called_once()
    request = urlopen.call_args[0][0]
    assert request.full_url == "https://abc123.appsync-api.us-east-1.amazonaws.com/event"
    body = json.loads(request.data)
    assert body["channel"] == "/projects/p1"
    envelope = json.loads(body["events"][0])
    assert envelope["type"] == "document.progress"
    assert envelope["seq"] == 3
    assert envelope["data"] == {"pagesDone": 1}
    assert envelope["at"].endswith("Z")


def test_fake_events_publisher_records_every_call() -> None:
    fake = FakeEventsPublisher()

    fake.publish("/projects/p1", "document.ready", {"documentId": "d1"}, seq=1)
    fake.publish("/projects/p1", "document.progress", {"pagesDone": 5}, seq=2)

    assert [e.event_type for e in fake.events] == ["document.ready", "document.progress"]
    assert fake.events[0].channel == "/projects/p1"
    assert fake.events[1].data == {"pagesDone": 5}
