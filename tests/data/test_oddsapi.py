import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from data.oddsapi import (
    OddsApiClient,
    RecordedReference,
    ReferenceSnapshot,
    parse_ml_fair,
)
from data.payloads import OddsApiOdds

_FIX = Path(__file__).parent.parent / "fixtures" / "oddsapi"


def _load() -> OddsApiOdds:
    return OddsApiOdds.model_validate(json.loads((_FIX / "odds_ml.json").read_text()))


def test_parse_ml_fair_devigs_betfair() -> None:
    ref = parse_ml_fair(_load(), "Betfair Exchange")
    assert ref is not None
    # implied = 1/2.10, 1/3.40, 1/3.70 -> normalized to sum 1.0
    total = sum(ref.fair.values())
    assert abs(total - 1.0) < 1e-9
    assert ref.fair["home"] > ref.fair["away"]  # 2.10 < 3.70
    assert ref.overround > 1.0
    assert ref.updated_at == datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


def test_parse_ml_fair_absent_bookmaker_returns_none() -> None:
    assert parse_ml_fair(_load(), "Pinnacle") is None


def test_parse_ml_fair_settled_event_returns_none() -> None:
    settled = OddsApiOdds.model_validate(
        {"eventId": "x", "updatedAt": "2026-06-21T12:00:00Z", "bookmakers": {}}
    )
    assert parse_ml_fair(settled, "Betfair Exchange") is None


def test_recorded_reference_is_as_of() -> None:
    t0 = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
    t1 = datetime(2026, 6, 21, 12, 5, tzinfo=UTC)
    ref = RecordedReference(
        [
            ReferenceSnapshot(market_id="m", ts=t0, fair=Decimal("0.60")),
            ReferenceSnapshot(market_id="m", ts=t1, fair=Decimal("0.62")),
        ]
    )
    assert ref.at("m", t0) == Decimal("0.60")
    assert ref.at("m", t1) == Decimal("0.62")
    assert ref.at("m", datetime(2026, 6, 21, 11, 0, tzinfo=UTC)) is None
    assert ref.at("unknown", t1) is None


def test_fetch_odds_over_mock_transport() -> None:
    async def _run() -> None:
        payload = json.loads((_FIX / "odds_ml.json").read_text())

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["market"] == "ML"
            assert "apiKey" in request.url.params
            return httpx.Response(200, json=payload)

        async with OddsApiClient(
            "secret", transport=httpx.MockTransport(handler), base_url="http://t"
        ) as client:
            odds = await client.fetch_odds("wc-2026-eng-fra", ["Betfair Exchange"])
        assert odds.eventId == "wc-2026-eng-fra"

    asyncio.run(_run())
