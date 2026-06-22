#!/usr/bin/env python
"""Poll odds-api /odds and append timestamped reference snapshots to JSON.

Human-run, hits the real network; NOT part of the test suite. Run with:

    uv run python scripts/record_reference_fixtures.py --event <id> --out snapshots.json

Reads ODDS_API_KEY from the environment. Records the Betfair-Exchange ML fair for
each configured (polymarket_market_id -> outcome) mapping, since /odds/movements
covers only Bet365. Append-only so repeated runs build a replayable history.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from data.oddsapi import OddsApiClient, parse_ml_fair

# Human-curated identity map: polymarket market_id -> odds-api ML outcome.
MARKET_TO_OUTCOME: dict[str, str] = {
    # "<polymarket_market_id>": "home",
}


async def _record(event_id: str, out: Path, bookmaker: str) -> int:
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        print("ODDS_API_KEY not set.")
        return 2
    async with OddsApiClient(key) as client:
        odds = await client.fetch_odds(event_id, [bookmaker])
    ref = parse_ml_fair(odds, bookmaker)
    if ref is None:
        print(f"no {bookmaker} ML for event {event_id} (settled or absent)")
        return 1
    ts = datetime.now(tz=UTC).isoformat()
    rows = json.loads(out.read_text()) if out.exists() else []
    for market_id, outcome in MARKET_TO_OUTCOME.items():
        rows.append(
            {
                "market_id": market_id,
                "ts": ts,
                "fair": str(Decimal(str(ref.fair[outcome]))),
            }
        )
    out.write_text(json.dumps(rows, indent=2))
    print(f"recorded {len(MARKET_TO_OUTCOME)} snapshot(s) at {ts}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--out", type=Path, default=Path("reference_snapshots.json"))
    parser.add_argument("--bookmaker", default="Betfair Exchange")
    args = parser.parse_args()
    return asyncio.run(_record(args.event, args.out, args.bookmaker))


if __name__ == "__main__":
    raise SystemExit(main())
