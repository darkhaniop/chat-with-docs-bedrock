"""`FakeEventsPublisher` — docs/08-testing.md's "dumb recorded-response player" philosophy
applied to `common.events.EventsPublisher`: a real AppSync Events channel doesn't exist to
capture responses from until Phase 6, so this fake just records calls rather than replaying
captured ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PublishedEvent:
    channel: str
    event_type: str
    data: dict[str, Any]
    seq: int


@dataclass
class FakeEventsPublisher:
    events: list[PublishedEvent] = field(default_factory=list)

    def publish(self, channel: str, event_type: str, data: dict[str, Any], *, seq: int) -> None:
        self.events.append(
            PublishedEvent(channel=channel, event_type=event_type, data=data, seq=seq)
        )
