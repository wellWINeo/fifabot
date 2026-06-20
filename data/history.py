"""Bulk historical loading via polars: quotes <-> DataFrame <-> ordered events.

The single place polars is used. float prices in the frame are for bulk
analytics; conversion back to Decimal happens when building events.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import polars as pl

from data.events import MarketEvent, Quote, event_from_quote


def quotes_to_frame(quotes: Sequence[Quote]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "market_id": [q.market_id for q in quotes],
            "ts": [q.ts for q in quotes],
            "price": [float(q.price) for q in quotes],
        }
    )


def frame_to_events(df: pl.DataFrame) -> list[MarketEvent]:
    events: list[MarketEvent] = []
    for row in df.sort("ts").iter_rows(named=True):
        quote = Quote(
            market_id=row["market_id"],
            ts=row["ts"],
            price=Decimal(str(row["price"])),
        )
        events.append(event_from_quote(quote))
    return events
