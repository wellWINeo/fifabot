"""Event feed: validate chronological ordering. First line of the look-ahead guard.

We reject out-of-order events rather than silently sorting, so a mis-ordered feed
cannot mask a look-ahead bug. Merging multiple markets into one chronological
stream is the caller's responsibility.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from data.events import MarketEvent


class LookAheadError(Exception):
    """Raised when data ordering would allow seeing the future."""


def load_events(events: Iterable[MarketEvent]) -> list[MarketEvent]:
    ordered: list[MarketEvent] = []
    previous: datetime | None = None
    for event in events:
        if previous is not None and event.ts < previous:
            raise LookAheadError(
                f"event ts {event.ts} precedes previous {previous}; "
                "events must be chronological"
            )
        ordered.append(event)
        previous = event.ts
    return ordered
