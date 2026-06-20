"""polars bulk-history conversions: quotes <-> frame <-> ordered events."""

from datetime import UTC, datetime
from decimal import Decimal

from data.events import Quote
from data.history import frame_to_events, quotes_to_frame


def _quotes() -> list[Quote]:
    return [
        Quote(
            market_id="m1",
            ts=datetime(2024, 6, 1, 0, 2, tzinfo=UTC),
            price=Decimal("0.47"),
        ),
        Quote(
            market_id="m1",
            ts=datetime(2024, 6, 1, 0, 1, tzinfo=UTC),
            price=Decimal("0.45"),
        ),
    ]


def test_frame_roundtrip_sorts_by_ts() -> None:
    df = quotes_to_frame(_quotes())
    events = frame_to_events(df)
    # frame_to_events must return chronological order regardless of input order.
    assert [e.ts.minute for e in events] == [1, 2]
    assert [e.quote.price for e in events] == [Decimal("0.45"), Decimal("0.47")]


def test_frame_has_expected_columns() -> None:
    df = quotes_to_frame(_quotes())
    assert set(df.columns) == {"market_id", "ts", "price"}
    assert df.height == 2
