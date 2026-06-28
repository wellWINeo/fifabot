"""FIFA market auto-discovery: pure classification + manifest build + thin client.

Discovery enumerates and classifies the World-Cup market universe; it does not
decide what to trade, label outcomes, or pull price history. Only
``discover_fifa_markets`` touches the network.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from data.events import DiscoveredMarket, DiscoveryManifest, MarketGroup
from data.gamma import GammaClient, parse_event_groups
from data.payloads import GammaEvent

_VS_PATTERNS = (" vs ", " vs. ", "-vs-")


def _event_kind(event: GammaEvent) -> str:
    text = f"{event.title} {event.slug}".lower()
    negrisk = event.negRisk or event.enableNegRisk
    n = len(event.markets)
    if negrisk and any(p in text for p in _VS_PATTERNS) and 2 <= n <= 3:
        return "match_moneyline"
    if negrisk and "group" in text and "winner" in text:
        return "group_winner"
    if negrisk and n >= 2:
        return "outright"
    if not negrisk and n == 1:
        return "prop"
    return "other"


def classify_event(event: GammaEvent) -> list[DiscoveredMarket]:
    kind = _event_kind(event)
    negrisk = event.negRisk or event.enableNegRisk
    group_id = event.id if negrisk and len(event.markets) >= 2 else None
    return [
        DiscoveredMarket(
            market_id=market.id,
            question=market.question,
            token_ids=tuple(market.clobTokenIds),
            event_slug=event.slug,
            kind=kind,
            group_id=group_id,
        )
        for market in event.markets
    ]


def build_manifest(
    topic: str,
    tag: str,
    events: Sequence[GammaEvent],
    discovered_at: datetime,
) -> DiscoveryManifest:
    markets: list[DiscoveredMarket] = []
    seen: set[str] = set()
    for event in events:
        for leg in classify_event(event):
            if leg.market_id in seen:
                continue
            seen.add(leg.market_id)
            markets.append(leg)
    markets.sort(key=lambda m: m.market_id)
    groups_seen: set[str] = set()
    unique_groups: list[MarketGroup] = []
    for event in events:
        for g in parse_event_groups(event):
            if g.group_id not in groups_seen:
                groups_seen.add(g.group_id)
                unique_groups.append(g)
    groups = tuple(unique_groups)
    return DiscoveryManifest(
        topic=topic,
        tag=tag,
        discovered_at=discovered_at,
        markets=tuple(markets),
        groups=groups,
    )


DISCOVERY_DIR = Path("var/discovery")


def write_manifest(manifest: DiscoveryManifest, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = manifest.discovered_at.strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{manifest.topic}-{stamp}.json"
    path.write_text(manifest.model_dump_json(indent=2))
    return path


def load_latest_manifest(directory: Path) -> DiscoveryManifest:
    files = sorted(directory.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"no discovery manifest in {directory}")
    return DiscoveryManifest.model_validate_json(files[-1].read_text())


class DiscoveryError(RuntimeError):
    """Raised when discovery returns an implausibly small market set."""


async def discover_fifa_markets(
    client: GammaClient,
    *,
    tag: str,
    tag_param: str = "tag",
    topic: str = "fifa-2026",
    directory: Path = DISCOVERY_DIR,
    now: datetime | None = None,
    min_markets: int = 1,
) -> DiscoveryManifest:
    events = await client.fetch_events(params={tag_param: tag})
    discovered_at = now or datetime.now(tz=UTC)
    manifest = build_manifest(topic, tag, events, discovered_at)
    if len(manifest.markets) < min_markets:
        raise DiscoveryError(
            f"discovered {len(manifest.markets)} markets for tag {tag!r}; "
            f"expected >= {min_markets}"
        )
    write_manifest(manifest, directory)
    return manifest
