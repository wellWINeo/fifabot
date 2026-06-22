"""odds-api.io reference adapter: parse ML odds -> de-vigged fair, replay snapshots.

The live OddsApiClient is human-run (ODDS_API_KEY from env); no test reaches it.
Backtests replay self-recorded snapshots through RecordedReference, which honors
the same as-of discipline as the harness.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Self

import httpx

from core.signals.devig import devig, overround
from data.http import RateLimiter, get_json
from data.payloads import OddsApiOdds

ODDS_API_BASE_URL = "https://api.odds-api.io/v3"
_OUTCOMES = ("home", "draw", "away")


@dataclass(frozen=True)
class ReferenceMl:
    fair: dict[str, float]
    overround: float
    updated_at: datetime


@dataclass(frozen=True)
class ReferenceSnapshot:
    market_id: str
    ts: datetime
    fair: Decimal


def parse_ml_fair(payload: OddsApiOdds, bookmaker: str) -> ReferenceMl | None:
    markets = payload.bookmakers.get(bookmaker)
    if not markets:
        return None
    ml = next((m for m in markets if m.name.upper() == "ML"), None)
    if ml is None or not ml.odds:
        return None
    row = ml.odds[0]
    try:
        decimals = [float(row[o]) for o in _OUTCOMES]
    except (KeyError, ValueError):
        return None
    if any(d <= 1.0 for d in decimals):
        return None
    implied = [1.0 / d for d in decimals]
    fair = devig(implied)
    return ReferenceMl(
        fair=dict(zip(_OUTCOMES, fair, strict=True)),
        overround=overround(implied),
        updated_at=payload.updatedAt,
    )


class RecordedReference:
    def __init__(self, snapshots: Sequence[ReferenceSnapshot]) -> None:
        self._by_market: dict[str, list[ReferenceSnapshot]] = {}
        for snap in sorted(snapshots, key=lambda s: s.ts):
            self._by_market.setdefault(snap.market_id, []).append(snap)

    def at(self, market_id: str, ts: datetime) -> Decimal | None:
        result: Decimal | None = None
        for snap in self._by_market.get(market_id, []):
            if snap.ts <= ts:
                result = snap.fair
            else:
                break
        return result


class OddsApiClient:
    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = ODDS_API_BASE_URL,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._key = api_key
        self._client = httpx.AsyncClient(base_url=base_url, transport=transport)
        self._limiter = limiter or RateLimiter()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def fetch_odds(self, event_id: str, bookmakers: Sequence[str]) -> OddsApiOdds:
        raw = await get_json(
            self._client,
            "/odds",
            {
                "apiKey": self._key,
                "eventId": event_id,
                "bookmakers": ",".join(bookmakers),
                "market": "ML",
            },
            limiter=self._limiter,
        )
        return OddsApiOdds.model_validate(raw)
