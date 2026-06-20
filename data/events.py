"""Canonical, timestamped records the backtest replays.

The data/<->backtest/ boundary: adapters parse raw payloads into these; the
harness only ever sees these. Decimal for prices; tz-aware UTC datetimes
(pydantic's AwareDatetime rejects naive timestamps at construction).
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Market(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_id: str
    question: str
    token_ids: tuple[str, ...]
    tick_size: Decimal = Field(gt=0)
    active: bool = True


class Quote(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_id: str
    ts: AwareDatetime
    price: Decimal = Field(gt=0, lt=1)
    bid: Decimal | None = None
    ask: Decimal | None = None
    size: Decimal | None = None


class MarketEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    ts: AwareDatetime
    market_id: str
    quote: Quote


def event_from_quote(quote: Quote) -> MarketEvent:
    return MarketEvent(ts=quote.ts, market_id=quote.market_id, quote=quote)
