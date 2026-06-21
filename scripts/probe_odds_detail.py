#!/usr/bin/env python
"""Focused follow-up probe: Betfair Exchange ML shape + /odds/movements edge cases.

Human-run, real network, not in the test suite. Run:

    uv run python scripts/probe_odds_detail.py

Answers two open questions from verify_odds_api.py:
  A. What does *Betfair Exchange* (our primary reference) return for ML, and how
     does its overround compare to Bet365? (exchange vs soft book)
  B. Is /odds/movements actually usable on the free plan? Tries several param
     shapes and reports each status, since a bare call 404s.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import httpx

from scripts.verify_odds_api import (
    FOOTBALL,
    Api,
    _pick_upcoming,
    as_list,
    devig_1x2,
    load_env_key,
)


def ml_markets(bookmaker_block: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(bookmaker_block, list):
        for market in bookmaker_block:
            if isinstance(market, dict) and str(market.get("name", "")).upper() == "ML":
                out.append(market)
    return out


def show(label: str, payload: Any, limit: int = 1200) -> None:
    text = json.dumps(payload, indent=2, default=str)
    if len(text) > limit:
        text = text[:limit] + f"\n  … ({len(text) - limit} more chars)"
    print(f"\n--- {label} ---")
    print("\n".join("  " + ln for ln in text.splitlines()))


def main() -> int:
    key = load_env_key()
    if not key:
        print("ODDS_API_KEY not set.")
        return 2

    with httpx.Client() as client:
        api = Api(client, key)
        events = as_list(api.get("/events", sport=FOOTBALL))
        event = _pick_upcoming(events)
        if not event:
            print("no upcoming event")
            return 1
        event_id = str(event["id"])
        print(
            f"event {event_id}: {event.get('home')} vs {event.get('away')} "
            f"status={event.get('status')!r} date={event.get('date')}"
        )

        # A. Per-bookmaker ML shape + de-vig --------------------------------
        odds = api.get("/odds", eventId=event_id, bookmakers="Betfair Exchange,Bet365")
        books = odds.get("bookmakers", {}) if isinstance(odds, dict) else {}
        print(f"\nbookmakers present in /odds: {list(books)}")
        for name in ("Betfair Exchange", "Bet365"):
            markets = ml_markets(books.get(name))
            if not markets:
                print(f"\n[{name}] no ML market returned")
                continue
            market = markets[0]
            show(f"{name} ML (raw)", market, limit=900)
            for row in as_list(market.get("odds")) or market.get("odds", []):
                if isinstance(row, dict) and {"home", "draw", "away"} <= {
                    k.lower() for k in row
                }:
                    h, d, a = (float(row[_k(row, x)]) for x in ("home", "draw", "away"))
                    fh, fd, fa, over = devig_1x2(h, d, a)
                    print(
                        f"  [{name}] decimal {h}/{d}/{a} → fair "
                        f"{fh:.3f}/{fd:.3f}/{fa:.3f}  overround={over:.4f}"
                    )

        # B. movements: try several shapes ----------------------------------
        print("\n=== /odds/movements attempts ===")
        attempts = [
            {"eventId": event_id, "bookmaker": "Betfair Exchange", "market": "ML"},
            {
                "eventId": event_id,
                "bookmaker": "Betfair Exchange",
                "market": "ML",
                "line": 0,
            },
            {"eventId": event_id, "bookmaker": "Bet365", "market": "ML"},
            {"eventId": event_id, "bookmaker": "Betfair Exchange"},
            {"eventId": event_id, "market": "ML"},
        ]
        for params in attempts:
            try:
                resp = api.get("/odds/movements", **params)
                series = as_list(resp) or (
                    resp.get("movements") if isinstance(resp, dict) else None
                )
                n = len(series) if isinstance(series, list) else "?"
                print(f"  OK   {params}  -> {n} point(s)")
                show("movements sample", resp, limit=500)
                break
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                print(f"  {code}  {params}  -> {exc.response.text[:120]}")

        # Also try /odds/updated (delta feed) as a self-recording fallback path
        print("\n=== /odds/updated probe (delta feed) ===")
        try:
            upd = api.get(
                "/odds/updated", since=0, bookmaker="Betfair Exchange", sport=FOOTBALL
            )
            show("/odds/updated", upd, limit=500)
        except httpx.HTTPStatusError as exc:
            print(f"  {exc.response.status_code}: {exc.response.text[:160]}")

    return 0


def _k(node: dict[str, Any], lowered: str) -> str:
    return next(k for k in node if k.lower() == lowered)


if __name__ == "__main__":
    sys.exit(main())
