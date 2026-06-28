"""Market auto-discovery: pure classify/build + orchestrator over MockTransport."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from data.discovery import (
    DiscoveryError,
    build_manifest,
    classify_event,
    discover_fifa_markets,
    load_latest_manifest,
    write_manifest,
)
from data.events import DiscoveryManifest
from data.gamma import GammaClient
from data.payloads import GammaEvent

_FIX = Path(__file__).parent.parent / "fixtures" / "gamma"


def _fifa_events() -> list[GammaEvent]:
    raw = json.loads((_FIX / "events_fifa.json").read_text())
    return [GammaEvent.model_validate(e) for e in raw]


def _by_id(event_id: str) -> GammaEvent:
    return next(e for e in _fifa_events() if e.id == event_id)


def test_classify_match_moneyline() -> None:
    legs = classify_event(_by_id("70001"))
    assert {leg.kind for leg in legs} == {"match_moneyline"}
    assert legs[0].token_ids == ("a1", "a2")
    assert all(leg.group_id == "70001" for leg in legs)
    assert legs[0].event_slug == "spain-vs-england-2026-06-28"


def test_classify_outright() -> None:
    legs = classify_event(_by_id("70002"))
    assert {leg.kind for leg in legs} == {"outright"}
    assert all(leg.group_id == "70002" for leg in legs)


def test_classify_group_winner() -> None:
    legs = classify_event(_by_id("70003"))
    assert {leg.kind for leg in legs} == {"group_winner"}


def test_classify_standalone_prop_has_no_group() -> None:
    legs = classify_event(_by_id("70004"))
    assert {leg.kind for leg in legs} == {"prop"}
    assert legs[0].group_id is None


def test_classify_never_raises_on_empty_event() -> None:
    empty = GammaEvent(id="z", slug="", title="", markets=[])
    assert classify_event(empty) == []


def test_build_manifest_collects_and_sorts_markets() -> None:
    manifest = build_manifest(
        "fifa-2026", "world-cup", _fifa_events(), datetime(2026, 6, 27, tzinfo=UTC)
    )
    ids = [m.market_id for m in manifest.markets]
    assert ids == sorted(ids)
    assert "800001" in ids and "800030" in ids


def test_build_manifest_dedups_by_market_id() -> None:
    events = _fifa_events()
    manifest = build_manifest(
        "fifa-2026", "world-cup", events + events, datetime(2026, 6, 27, tzinfo=UTC)
    )
    ids = [m.market_id for m in manifest.markets]
    assert len(ids) == len(set(ids))
    group_ids = [g.group_id for g in manifest.groups]
    assert len(group_ids) == len(set(group_ids))


def test_build_manifest_groups_reference_known_markets() -> None:
    manifest = build_manifest(
        "fifa-2026", "world-cup", _fifa_events(), datetime(2026, 6, 27, tzinfo=UTC)
    )
    known = {m.market_id for m in manifest.markets}
    for group in manifest.groups:
        for market_id in group.market_ids:
            assert market_id in known
    assert all(g.group_id != "70004" for g in manifest.groups)


def _manifest(stamp: datetime) -> "DiscoveryManifest":
    return build_manifest("fifa-2026", "world-cup", _fifa_events(), stamp)


def test_write_then_load_latest_roundtrips(tmp_path: Path) -> None:
    early = _manifest(datetime(2026, 6, 27, 9, 0, tzinfo=UTC))
    late = _manifest(datetime(2026, 6, 27, 18, 0, tzinfo=UTC))
    write_manifest(early, tmp_path)
    write_manifest(late, tmp_path)
    loaded = load_latest_manifest(tmp_path)
    assert loaded.discovered_at == late.discovered_at
    assert [m.market_id for m in loaded.markets] == [m.market_id for m in late.markets]


def test_write_manifest_is_append_only(tmp_path: Path) -> None:
    write_manifest(_manifest(datetime(2026, 6, 27, 9, 0, tzinfo=UTC)), tmp_path)
    write_manifest(_manifest(datetime(2026, 6, 27, 10, 0, tzinfo=UTC)), tmp_path)
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_discover_builds_and_persists_manifest(tmp_path: Path) -> None:
    async def _run() -> None:
        payload = json.loads((_FIX / "events_fifa.json").read_text())
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.url.params))
            offset = int(request.url.params["offset"])
            return httpx.Response(200, json=payload if offset == 0 else [])

        async with GammaClient(
            transport=httpx.MockTransport(handler), base_url="http://t"
        ) as client:
            manifest = await discover_fifa_markets(
                client,
                tag="world-cup",
                directory=tmp_path,
                now=datetime(2026, 6, 27, tzinfo=UTC),
            )
        assert seen["tag"] == "world-cup"
        assert any(m.kind == "match_moneyline" for m in manifest.markets)
        assert len(list(tmp_path.glob("*.json"))) == 1

    asyncio.run(_run())


def test_discover_raises_on_empty_result(tmp_path: Path) -> None:
    async def _run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        async with GammaClient(
            transport=httpx.MockTransport(handler), base_url="http://t"
        ) as client:
            with pytest.raises(DiscoveryError):
                await discover_fifa_markets(client, tag="bogus-tag", directory=tmp_path)

    asyncio.run(_run())
    assert len(list(tmp_path.glob("*.json"))) == 0


def test_discover_raises_below_floor(tmp_path: Path) -> None:
    async def _run() -> None:
        payload = json.loads((_FIX / "events_fifa.json").read_text())

        def handler(request: httpx.Request) -> httpx.Response:
            offset = int(request.url.params["offset"])
            return httpx.Response(200, json=payload if offset == 0 else [])

        async with GammaClient(
            transport=httpx.MockTransport(handler), base_url="http://t"
        ) as client:
            with pytest.raises(DiscoveryError):
                await discover_fifa_markets(
                    client, tag="world-cup", directory=tmp_path, min_markets=999
                )

    asyncio.run(_run())
    assert len(list(tmp_path.glob("*.json"))) == 0
