#!/usr/bin/env python
"""Throwaway probe for Polymarket's Gamma API — confirms it's public/no-auth and
reveals the event->markets grouping shape S2 needs.

Human-run, real network, NOT part of the test suite. Run:

    uv run python scripts/probe_gamma.py

Answers:
  1. Is /events reachable with NO auth header? (and is an Authorization header
     simply ignored?)  -> proves public read-only access
  2. What does an event look like — does it nest a `markets` array, and how are
     mutually-exclusive legs represented (separate markets w/ token ids, a
     negRisk flag, outcomes)?  -> the shape a parse_event_groups would target
  3. Can we find a soccer / FIFA World Cup event and see its leg structure?
"""

from __future__ import annotations

import json
import sys
from typing import Any

import httpx

GAMMA = "https://gamma-api.polymarket.com"
# grouping-relevant fields to surface from each nested market
MARKET_KEYS = (
    "id",
    "question",
    "groupItemTitle",
    "outcomes",
    "clobTokenIds",
    "negRisk",
)


def show(label: str, payload: Any, limit: int = 1100) -> None:
    text = json.dumps(payload, indent=2, default=str)
    if len(text) > limit:
        text = text[:limit] + f"\n  … ({len(text) - limit} more chars)"
    print(f"\n--- {label} ---")
    print("\n".join("  " + ln for ln in text.splitlines()))


def keys_of(obj: Any) -> list[str]:
    return sorted(obj.keys()) if isinstance(obj, dict) else [f"<{type(obj).__name__}>"]


def trim_market(m: dict[str, Any]) -> dict[str, Any]:
    return {k: m.get(k) for k in MARKET_KEYS if k in m}


def main() -> int:
    with httpx.Client(base_url=GAMMA, timeout=30.0) as client:
        # 1. public / no-auth ------------------------------------------------
        print("1. Public access (no auth)")
        r = client.get("/events", params={"closed": "false", "limit": 5})
        print(f"  GET /events (no headers) -> HTTP {r.status_code}")
        r.raise_for_status()
        events = r.json()
        print(f"  returned {len(events)} event(s); top-level event keys:")
        if events:
            print("   ", keys_of(events[0]))

        # does a bogus auth header change anything? (expect: ignored)
        r2 = client.get(
            "/events",
            params={"closed": "false", "limit": 1},
            headers={"Authorization": "Bearer this-is-not-a-real-token"},
        )
        print(
            f"  GET /events (bogus auth header) -> HTTP {r2.status_code} "
            f"(same as no-auth => header ignored, API is public)"
        )

        # 2. event -> markets grouping shape --------------------------------
        print("\n2. Event grouping shape")
        multi = next(
            (
                e
                for e in events
                if isinstance(e.get("markets"), list) and len(e["markets"]) > 1
            ),
            events[0] if events else None,
        )
        if multi:
            print(f"  sample event: id={multi.get('id')} slug={multi.get('slug')!r}")
            print(f"    title: {multi.get('title')!r}")
            print(
                f"    negRisk(event)={multi.get('negRisk')}  "
                f"#markets={len(multi.get('markets', []))}"
            )
            for m in multi.get("markets", [])[:4]:
                show("nested market (grouping fields)", trim_market(m), limit=500)

        # 3. find a soccer / World Cup event --------------------------------
        print("\n3. Soccer / FIFA World Cup search")
        for params in (
            {"closed": "false", "limit": 200, "tag": "soccer"},
            {"closed": "false", "limit": 200},
        ):
            try:
                batch = client.get("/events", params=params).json()
            except httpx.HTTPStatusError:
                continue
            hits = [
                e
                for e in batch
                if isinstance(e, dict)
                and any(
                    term in f"{e.get('title', '')} {e.get('slug', '')}".lower()
                    for term in ("world cup", "fifa", " vs ", "-vs-")
                )
            ]
            print(f"  params={params} -> {len(batch)} events, {len(hits)} soccer-ish")
            if hits:
                ev = hits[0]
                print(
                    f"  example: id={ev.get('id')} title={ev.get('title')!r} "
                    f"negRisk={ev.get('negRisk')} #markets={len(ev.get('markets', []))}"
                )
                for m in ev.get("markets", [])[:4]:
                    show("WC-ish nested market", trim_market(m), limit=500)
                break
        else:
            print("  no soccer-ish event found in scanned pages (off-window?)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
