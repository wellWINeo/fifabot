#!/usr/bin/env python
"""Throwaway probe for odds-api.io — confirms the API supports what S1 needs.

Human-run, hits the real network; NOT part of the test suite. Run with:

    uv run python scripts/verify_odds_api.py

Reads ODDS_API_KEY from the environment or a local .env file. Verifies, in order:
  1. key/auth works and which 2 bookmakers are selected on the free plan
  2. exact identifiers for "Betfair Exchange" and "Bet365" in /bookmakers
  3. the football sport slug and a (World Cup, if available) event id
  4. the ML (1X2) market returns home/draw/away  -> de-vig demo
  5. /odds/movements returns a timestamped series on the free plan (history)

Each check prints PASS / WARN / FAIL and a small raw sample so you can see the
real JSON shape. Failures don't stop later checks. The API key is never printed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://api.odds-api.io/v3"
FOOTBALL = "football"
WANTED = ("betfair exchange", "bet365")

GREEN, YELLOW, RED, DIM, RESET = (
    "\033[32m",
    "\033[33m",
    "\033[31m",
    "\033[2m",
    "\033[0m",
)


def load_env_key() -> str | None:
    """ODDS_API_KEY from the environment, falling back to a minimal .env parse."""
    key = os.environ.get("ODDS_API_KEY")
    if key:
        return key.strip()
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == "ODDS_API_KEY":
            return value.strip().strip("'\"") or None
    return None


def status(label: str, ok: bool | None, detail: str = "") -> None:
    mark = {
        True: f"{GREEN}PASS{RESET}",
        False: f"{RED}FAIL{RESET}",
        None: f"{YELLOW}WARN{RESET}",
    }[ok]
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))


def preview(obj: Any, limit: int = 600) -> None:
    text = json.dumps(obj, indent=2, default=str)
    if len(text) > limit:
        text = text[:limit] + f"\n    … ({len(text) - limit} more chars)"
    print(DIM + "\n".join("    " + ln for ln in text.splitlines()) + RESET)


def as_list(payload: Any) -> list[Any]:
    """Tolerate bare lists or common envelope shapes ({data|results|events: [...]})."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "results", "events", "leagues", "bookmakers", "sports"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def find_named(items: list[Any], needle: str) -> dict[str, Any] | None:
    for item in items:
        if not isinstance(item, dict):
            continue
        blob = " ".join(str(v) for v in item.values()).lower()
        if needle in blob:
            return item
    return None


def identifier(bookmaker: dict[str, Any]) -> str:
    """Best-guess request identifier; prints the object so you can confirm."""
    for field in ("slug", "id", "key", "name"):
        if bookmaker.get(field):
            return str(bookmaker[field])
    return ""


def devig_1x2(
    home: float, draw: float, away: float
) -> tuple[float, float, float, float]:
    implied = [1 / home, 1 / draw, 1 / away]
    overround = sum(implied)
    fair = tuple(p / overround for p in implied)
    return fair[0], fair[1], fair[2], overround


class Api:
    def __init__(self, client: httpx.Client, key: str) -> None:
        self._client = client
        self._key = key

    def get(self, path: str, **params: Any) -> Any:
        params["apiKey"] = self._key
        resp = self._client.get(f"{BASE_URL}{path}", params=params, timeout=30.0)
        rate = {k: v for k, v in resp.headers.items() if "ratelimit" in k.lower()}
        if rate:
            print(DIM + f"    rate-limit headers: {rate}" + RESET)
        resp.raise_for_status()
        return resp.json()


def main() -> int:
    key = load_env_key()
    if not key:
        print(f"{RED}ODDS_API_KEY not set.{RESET} Put it in .env, then re-run.")
        return 2

    failures = 0
    with httpx.Client() as client:
        api = Api(client, key)

        # 1. auth + selected bookmakers ------------------------------------
        print("\n1. Auth & selected bookmakers (/bookmakers/selected)")
        selected_ids: list[str] = []
        try:
            selected = api.get("/bookmakers/selected")
            items = as_list(selected) or (
                selected if isinstance(selected, list) else []
            )
            selected_ids = [identifier(b) for b in items if isinstance(b, dict)]
            status("key accepted", True, f"{len(items)} bookmaker(s) selected")
            preview(selected)
        except httpx.HTTPStatusError as exc:
            failures += 1
            status(
                "key accepted",
                False,
                f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            )

        # 2. catalog has Betfair Exchange + Bet365 -------------------------
        print("\n2. Catalog identifiers (/bookmakers)")
        try:
            catalog = as_list(api.get("/bookmakers"))
            for needle in WANTED:
                found = find_named(catalog, needle)
                if found:
                    status(
                        f"'{needle}' present",
                        True,
                        f"request id ≈ '{identifier(found)}'",
                    )
                    preview(found, limit=300)
                else:
                    failures += 1
                    status(f"'{needle}' present", False, "not found in catalog")
        except httpx.HTTPStatusError as exc:
            failures += 1
            status("/bookmakers reachable", False, f"HTTP {exc.response.status_code}")

        # 3. football slug + an event (prefer World Cup) -------------------
        print("\n3. Football leagues & events")
        event_id: str | None = None
        try:
            leagues = as_list(api.get("/leagues", sport=FOOTBALL))
            wc = find_named(leagues, "world cup")
            status("football leagues", True, f"{len(leagues)} leagues")
            if wc:
                status("World Cup league", True, f"{identifier(wc)}")
                preview(wc, limit=300)
            else:
                status(
                    "World Cup league", None, "not in current league list (off-season?)"
                )
        except httpx.HTTPStatusError as exc:
            failures += 1
            status("/leagues reachable", False, f"HTTP {exc.response.status_code}")

        try:
            events = as_list(api.get("/events", sport=FOOTBALL))
            status("football events", bool(events), f"{len(events)} event(s)")
            chosen = _pick_upcoming(events)
            if chosen:
                event_id = identifier(chosen)
                in_wc = "world cup" in str(chosen.get("league", "")).lower()
                tag = "upcoming World Cup" if in_wc else "upcoming"
                st = chosen.get("status")
                print(f"    using {tag} event id={event_id} status={st!r}")
                preview(chosen, limit=400)
            else:
                status(
                    "upcoming event",
                    None,
                    "no upcoming event found — odds/movements may be empty",
                )
        except httpx.HTTPStatusError as exc:
            failures += 1
            status("/events reachable", False, f"HTTP {exc.response.status_code}")

        bookmakers_param = (
            ",".join(selected_ids) if selected_ids else "Betfair Exchange,Bet365"
        )

        # 4. ML (1X2) odds + de-vig ----------------------------------------
        print("\n4. ML (1X2) market & de-vig (/odds)")
        if event_id:
            try:
                odds = api.get("/odds", eventId=event_id, bookmakers=bookmakers_param)
                preview(odds, limit=800)
                triplet = _extract_1x2(odds)
                if triplet:
                    h, d, a = triplet
                    fh, fd, fa, over = devig_1x2(h, d, a)
                    status("ML home/draw/away present", True, f"odds {h}/{d}/{a}")
                    print(
                        f"    de-vig → fair P(home)={fh:.3f} P(draw)={fd:.3f} "
                        f"P(away)={fa:.3f}  overround={over:.3f}"
                    )
                else:
                    status(
                        "ML home/draw/away present",
                        None,
                        "couldn't auto-locate 1X2 — inspect sample above",
                    )
            except httpx.HTTPStatusError as exc:
                failures += 1
                status("/odds reachable", False, f"HTTP {exc.response.status_code}")
        else:
            status("odds check", None, "skipped — no event id")

        # 5. movements / history on free plan ------------------------------
        print("\n5. Historical series (/odds/movements)")
        if event_id:
            try:
                moves = api.get(
                    "/odds/movements",
                    eventId=event_id,
                    bookmaker=(selected_ids[0] if selected_ids else "Betfair Exchange"),
                    market="ML",
                )
                series = as_list(moves) or (
                    moves.get("movements") if isinstance(moves, dict) else []
                )
                status(
                    "movements on free plan", bool(series), f"{len(series)} point(s)"
                )
                preview(moves, limit=500)
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                # 402/403 here = endpoint is paid-only; that's a finding, not a crash.
                status(
                    "movements on free plan",
                    None if code in (402, 403) else False,
                    f"HTTP {code}: {exc.response.text[:160]}",
                )
                if code not in (402, 403):
                    failures += 1
        else:
            status("movements check", None, "skipped — no event id")

    print("\n" + ("=" * 60))
    if failures:
        print(f"{RED}{failures} hard check(s) failed.{RESET} Review output above.")
    else:
        print(f"{GREEN}All hard checks passed{RESET} (review WARN lines for caveats).")
    return 1 if failures else 0


def _extract_1x2(odds: Any) -> tuple[float, float, float] | None:
    """Best-effort hunt for home/draw/away decimals anywhere in the odds payload."""
    found: tuple[float, float, float] | None = None

    def walk(node: Any) -> None:
        nonlocal found
        if found is not None:
            return
        if isinstance(node, dict):
            keys = {k.lower() for k in node}
            if {"home", "draw", "away"} <= keys:
                try:
                    found = (
                        float(node[_match(node, "home")]),
                        float(node[_match(node, "draw")]),
                        float(node[_match(node, "away")]),
                    )
                    return
                except (TypeError, ValueError):
                    pass
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(odds)
    return found


def _match(node: dict[str, Any], lowered: str) -> str:
    return next(k for k in node if k.lower() == lowered)


_DONE = {"settled", "finished", "ended", "closed", "cancelled", "canceled", "postponed"}


def _pick_upcoming(events: list[Any]) -> dict[str, Any] | None:
    """Prefer an upcoming World Cup event; fall back to any upcoming, then any."""
    live = [
        e
        for e in events
        if isinstance(e, dict) and str(e.get("status", "")).lower() not in _DONE
    ]
    wc_live = [e for e in live if "world cup" in str(e.get("league", "")).lower()]
    for bucket in (wc_live, live, [e for e in events if isinstance(e, dict)]):
        if bucket:
            return bucket[0]
    return None


if __name__ == "__main__":
    sys.exit(main())
