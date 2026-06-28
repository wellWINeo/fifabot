# FIFA 2026 Market Auto-Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enumerate and classify the 2026 FIFA World Cup market universe on Polymarket into a persisted, tagged manifest that downstream price-collection and backtesting can consume.

**Architecture:** A new `data/discovery.py` module follows the existing `data/` hexagonal pattern — pure parsers/classifiers (`classify_event`, `build_manifest`) plus a single thin network-touching orchestrator (`discover_fifa_markets`). It reuses the existing `GammaClient` and `parse_event_groups`. Output is an append-only JSON snapshot.

**Tech Stack:** Python 3.12+, `uv`, `httpx` (async, `MockTransport` in tests), `pydantic` v2 (frozen models), `pytest`.

## Global Constraints

- Python 3.12+, managed with `uv`. Run tests with `uv run pytest`.
- TDD always: failing test first; a step is done only when its tests pass plus `uv run ruff check .` and `uv run mypy .` are clean.
- No real network in unit tests — use `httpx.MockTransport` and recorded fixtures. The autouse guard in `tests/conftest.py` blocks sockets.
- Pure core / thin edges: only `discover_fifa_markets` touches the network. `classify_event` and `build_manifest` are pure and never raise on odd input.
- Canonical records are frozen pydantic models in `data/events.py` (`model_config = ConfigDict(frozen=True)`); wire-shape models in `data/payloads.py` use `extra="ignore"` and `# noqa: N815` for camelCase field names.
- Secrets never in code/tests. The Gamma API is public (no auth).

---

## Task 0: Probe Gamma to pin live-API facts (manual prerequisite, no TDD)

This is a human-run exploration against the real network — **not** a unit test. It pins three values the rest of the plan assumes. Do this first; record findings in the spec's "Open items" section.

**Files:**
- Modify: `scripts/probe_gamma.py` (extend the existing throwaway probe)

- [ ] **Step 1: Extend the probe to answer three questions**

Add to `scripts/probe_gamma.py`'s `main()`:
1. Confirm the tag/series filter param. Try, and print result counts for, each of:
   `client.get("/events", params={"tag": "world-cup"})`,
   `params={"tag_id": "<id from a tagged event>"}`,
   `params={"series_id": "<id>"}`. Identify which actually returns a *filtered* set.
2. For one nested market inside a returned event, print the raw types of
   `clobTokenIds`, `outcomes`, and whether `tickSize`/`minimumOrderSize`/`closed`/
   `startDate`/`gameStartTime`/`sportsMarketType`/`gameId` are present.
3. Find a real match event and a real outright event; print their `negRisk`,
   `#markets`, `title`, `slug`, and any field that distinguishes a 3-way match
   from a 3-leg outright.

- [ ] **Step 2: Run it (human-run, real network)**

Run: `uv run python scripts/probe_gamma.py`
Expected: prints the filtering param that works, the nested-market field shape, and whether a structured match discriminator exists.

- [ ] **Step 3: Record findings**

Edit `docs/superpowers/specs/2026-06-27-fifa-market-discovery-design.md` "Open items":
fill in the confirmed tag param key, the nested field shape, and — explicitly —
whether a structured `match_moneyline` discriminator exists. If none exists, note
that Task 4's classifier stays best-effort (title `vs`-pattern).

- [ ] **Step 4: Commit**

```bash
git add scripts/probe_gamma.py docs/superpowers/specs/2026-06-27-fifa-market-discovery-design.md
git commit -m "chore: probe Gamma to pin discovery API facts"
```

> If Step 1 shows the working param key is not `tag`, set Task 7's `tag_param` default accordingly (e.g. `series_id`). If Step 2 shows a structured match field exists, prefer it in Task 4's `_event_kind` (add it as the first check) and adjust that task's fixture/tests accordingly.

---

## Task 1: Decode event-nested `clobTokenIds` JSON-string

The `/events` payload encodes nested-market `clobTokenIds` as a JSON **string** (`"[\"4394\", \"1126\"]"`), unlike the top-level `/markets` list form. `GammaEventMarket` must decode it.

**Files:**
- Modify: `data/payloads.py:69-75` (`GammaEventMarket`)
- Test: `tests/data/test_payloads.py`

**Interfaces:**
- Produces: `GammaEventMarket.clobTokenIds: list[str]` (decoded from a JSON string or accepted as a list).

- [ ] **Step 1: Write the failing test**

Add to `tests/data/test_payloads.py`:

```python
import json

import pytest
from pydantic import ValidationError

from data.payloads import GammaEventMarket


def test_event_market_decodes_json_string_token_ids() -> None:
    m = GammaEventMarket.model_validate(
        {"id": "1", "question": "q", "clobTokenIds": "[\"4394\", \"1126\"]"}
    )
    assert m.clobTokenIds == ["4394", "1126"]


def test_event_market_accepts_list_token_ids() -> None:
    m = GammaEventMarket.model_validate(
        {"id": "1", "question": "q", "clobTokenIds": ["a", "b"]}
    )
    assert m.clobTokenIds == ["a", "b"]


def test_event_market_defaults_empty_token_ids() -> None:
    m = GammaEventMarket.model_validate({"id": "1", "question": "q"})
    assert m.clobTokenIds == []


def test_event_market_rejects_malformed_token_ids() -> None:
    with pytest.raises(ValidationError):
        GammaEventMarket.model_validate(
            {"id": "1", "question": "q", "clobTokenIds": "not-json"}
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/data/test_payloads.py -k event_market -v`
Expected: FAIL (`clobTokenIds` not a field / no decoding).

- [ ] **Step 3: Write minimal implementation**

In `data/payloads.py`, add the import and a `mode="before"` validator. Update `GammaEventMarket`:

```python
import json

from pydantic import field_validator


class GammaEventMarket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    question: str
    groupItemTitle: str = ""  # noqa: N815
    clobTokenIds: list[str] = Field(default_factory=list)  # noqa: N815

    @field_validator("clobTokenIds", mode="before")
    @classmethod
    def _decode_token_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return json.loads(value)
        return value
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/data/test_payloads.py -k event_market -v`
Expected: PASS (4 tests). Note: a malformed string makes `json.loads` raise `JSONDecodeError`, which pydantic wraps as `ValidationError`.

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check . && uv run mypy .
git add data/payloads.py tests/data/test_payloads.py
git commit -m "feat: decode event-nested clobTokenIds JSON-string in GammaEventMarket"
```

---

## Task 2: Optional `params` filter on `GammaClient.fetch_events`

Discovery filters `/events` by the World-Cup tag. `fetch_events` currently hardcodes `{limit, offset}`; add merged extra params without breaking the existing `fetch_event_groups` caller.

**Files:**
- Modify: `data/gamma.py:98-115` (`fetch_events`)
- Test: `tests/data/test_gamma.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `GammaClient.fetch_events(*, limit: int = 100, params: Mapping[str, str] | None = None) -> list[GammaEvent]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/data/test_gamma.py`:

```python
def test_fetch_events_sends_extra_params_across_pages() -> None:
    async def _run() -> None:
        all_events = json.loads((_FIX / "events_negrisk.json").read_text())
        tags_seen: list[str] = []
        offsets_seen: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            tags_seen.append(request.url.params["tag"])
            offset = int(request.url.params["offset"])
            limit = int(request.url.params["limit"])
            offsets_seen.append(offset)
            return httpx.Response(200, json=all_events[offset : offset + limit])

        async with GammaClient(
            transport=httpx.MockTransport(handler), base_url="http://t"
        ) as client:
            events = await client.fetch_events(limit=2, params={"tag": "world-cup"})
        # 3-event fixture at limit 2 -> two real pages; tag must persist on both
        assert offsets_seen == [0, 2]
        assert tags_seen == ["world-cup", "world-cup"]
        assert len(events) == 3

    asyncio.run(_run())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/data/test_gamma.py::test_fetch_events_sends_extra_params_across_pages -v`
Expected: FAIL (`fetch_events` has no `params` keyword → `TypeError`).

- [ ] **Step 3: Write minimal implementation**

In `data/gamma.py`, change `fetch_events` (keep `Mapping` import — already imported):

```python
    async def fetch_events(
        self, *, limit: int = 100, params: Mapping[str, str] | None = None
    ) -> list[GammaEvent]:
        events: list[GammaEvent] = []
        offset = 0
        while True:
            query: dict[str, object] = {"limit": limit, "offset": offset}
            if params:
                query.update(params)
            raw = await get_json(
                self._client,
                "/events",
                query,
                limiter=self._limiter,
                max_retries=self._max_retries,
                retry_backoff=self._retry_backoff,
            )
            page = [GammaEvent.model_validate(e) for e in raw]
            events.extend(page)
            if len(page) < limit:
                break
            offset += limit
        return events
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/data/test_gamma.py -v`
Expected: PASS — the new test and the existing `test_fetch_event_groups_over_mock_transport` (regression: default call unchanged).

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check . && uv run mypy .
git add data/gamma.py tests/data/test_gamma.py
git commit -m "feat: optional params filter on GammaClient.fetch_events"
```

---

## Task 3: `DiscoveredMarket` and `DiscoveryManifest` records

The canonical, frozen outputs of discovery.

**Files:**
- Modify: `data/events.py` (append two models)
- Test: `tests/data/test_events.py`

**Interfaces:**
- Produces:
  - `DiscoveredMarket(market_id: str, question: str, token_ids: tuple[str, ...], event_slug: str, kind: str, group_id: str | None = None)` — frozen.
  - `DiscoveryManifest(topic: str, tag: str, discovered_at: AwareDatetime, markets: tuple[DiscoveredMarket, ...], groups: tuple[MarketGroup, ...])` — frozen.

- [ ] **Step 1: Write the failing test**

Add to `tests/data/test_events.py`:

```python
from data.events import DiscoveredMarket, DiscoveryManifest


def test_discovered_market_construction() -> None:
    dm = DiscoveredMarket(
        market_id="558934",
        question="Will Spain win the 2026 FIFA World Cup?",
        token_ids=("4394", "1126"),
        event_slug="world-cup-winner",
        kind="outright",
        group_id="30615",
    )
    assert dm.group_id == "30615"
    assert dm.kind == "outright"


def test_discovered_market_group_id_optional() -> None:
    dm = DiscoveredMarket(
        market_id="x", question="q", token_ids=("a",), event_slug="s", kind="prop"
    )
    assert dm.group_id is None


def test_discovered_market_is_frozen() -> None:
    dm = DiscoveredMarket(
        market_id="x", question="q", token_ids=("a",), event_slug="s", kind="prop"
    )
    with pytest.raises(ValidationError):
        dm.kind = "other"  # type: ignore[misc]


def test_discovery_manifest_holds_markets_and_groups() -> None:
    dm = DiscoveredMarket(
        market_id="x", question="q", token_ids=("a",), event_slug="s", kind="prop"
    )
    manifest = DiscoveryManifest(
        topic="fifa-2026",
        tag="world-cup",
        discovered_at=datetime(2026, 6, 27, 12, 0, tzinfo=UTC),
        markets=(dm,),
        groups=(),
    )
    assert manifest.markets[0].market_id == "x"
    assert manifest.tag == "world-cup"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/data/test_events.py -k discover -v`
Expected: FAIL (`ImportError` — models don't exist).

- [ ] **Step 3: Write minimal implementation**

Append to `data/events.py`:

```python
class DiscoveredMarket(BaseModel):
    """One enumerated, classified market leg (output of auto-discovery)."""

    model_config = ConfigDict(frozen=True)

    market_id: str
    question: str
    token_ids: tuple[str, ...]
    event_slug: str
    kind: str
    group_id: str | None = None


class DiscoveryManifest(BaseModel):
    """A persisted snapshot of a topic's discovered market universe."""

    model_config = ConfigDict(frozen=True)

    topic: str
    tag: str
    discovered_at: AwareDatetime
    markets: tuple[DiscoveredMarket, ...]
    groups: tuple[MarketGroup, ...]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/data/test_events.py -k discover -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check . && uv run mypy .
git add data/events.py tests/data/test_events.py
git commit -m "feat: DiscoveredMarket and DiscoveryManifest records"
```

---

## Task 4: Mixed FIFA fixture + pure `classify_event`

Classify one event's legs into `DiscoveredMarket[]`. `kind="other"` is the safe default; `match_moneyline` is best-effort via a title `vs`-pattern (per spec, pending a structured discriminator from Task 0). Never raises.

**Files:**
- Create: `tests/fixtures/gamma/events_fifa.json`
- Create: `data/discovery.py`
- Test: `tests/data/test_discovery.py`

**Interfaces:**
- Consumes: `GammaEvent` (`data/payloads.py`), `DiscoveredMarket` (Task 3).
- Produces: `classify_event(event: GammaEvent) -> list[DiscoveredMarket]`.

> Note: `test_classify_match_moneyline`'s `token_ids == ("a1", "a2")` assertion
> depends on Task 1's JSON-string decoder. If Tasks are done out of order, this
> test fails on token decoding (not just classification) until Task 1 lands.

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/gamma/events_fifa.json`:

```json
[
  {
    "id": "70001", "slug": "spain-vs-england-2026-06-28", "title": "Spain vs England",
    "negRisk": true, "enableNegRisk": true,
    "markets": [
      {"id": "800001", "question": "Will Spain beat England?", "groupItemTitle": "Spain", "clobTokenIds": "[\"a1\", \"a2\"]"},
      {"id": "800002", "question": "Will Spain vs England draw?", "groupItemTitle": "Draw", "clobTokenIds": "[\"b1\", \"b2\"]"},
      {"id": "800003", "question": "Will England beat Spain?", "groupItemTitle": "England", "clobTokenIds": "[\"c1\", \"c2\"]"}
    ]
  },
  {
    "id": "70002", "slug": "world-cup-winner", "title": "2026 World Cup Winner",
    "negRisk": true, "enableNegRisk": true,
    "markets": [
      {"id": "800010", "question": "Will Spain win the World Cup?", "groupItemTitle": "Spain", "clobTokenIds": "[\"d1\", \"d2\"]"},
      {"id": "800011", "question": "Will Brazil win the World Cup?", "groupItemTitle": "Brazil", "clobTokenIds": "[\"e1\", \"e2\"]"},
      {"id": "800012", "question": "Will France win the World Cup?", "groupItemTitle": "France", "clobTokenIds": "[\"f1\", \"f2\"]"},
      {"id": "800013", "question": "Will England win the World Cup?", "groupItemTitle": "England", "clobTokenIds": "[\"g1\", \"g2\"]"}
    ]
  },
  {
    "id": "70003", "slug": "world-cup-group-a-winner", "title": "World Cup Group A Winner",
    "negRisk": true, "enableNegRisk": true,
    "markets": [
      {"id": "800020", "question": "Will Mexico win Group A?", "groupItemTitle": "Mexico", "clobTokenIds": "[\"h1\", \"h2\"]"},
      {"id": "800021", "question": "Will Canada win Group A?", "groupItemTitle": "Canada", "clobTokenIds": "[\"i1\", \"i2\"]"}
    ]
  },
  {
    "id": "70004", "slug": "wc-most-corners-final", "title": "Will the World Cup final have over 10 corners?",
    "negRisk": false, "enableNegRisk": false,
    "markets": [
      {"id": "800030", "question": "Over 10 corners in the final?", "groupItemTitle": "", "clobTokenIds": "[\"j1\", \"j2\"]"}
    ]
  }
]
```

- [ ] **Step 2: Write the failing test**

Create `tests/data/test_discovery.py`:

```python
"""Market auto-discovery: pure classify/build + orchestrator over MockTransport."""

import json
from pathlib import Path

from data.discovery import classify_event
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/data/test_discovery.py -v`
Expected: FAIL (`ImportError` — `data/discovery.py` doesn't exist).

- [ ] **Step 4: Write minimal implementation**

Create `data/discovery.py`:

```python
"""FIFA market auto-discovery: pure classification + manifest build + thin client.

Discovery enumerates and classifies the World-Cup market universe; it does not
decide what to trade, label outcomes, or pull price history. Only
``discover_fifa_markets`` touches the network.
"""

from __future__ import annotations

from data.events import DiscoveredMarket
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/data/test_discovery.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run ruff check . && uv run mypy .
git add data/discovery.py tests/data/test_discovery.py tests/fixtures/gamma/events_fifa.json
git commit -m "feat: classify_event + FIFA discovery fixture"
```

---

## Task 5: Pure `build_manifest` (dedup, sort, attach groups)

Aggregate classified legs across events into a `DiscoveryManifest`: dedup by `market_id`, deterministic ordering, and the negRisk `MarketGroup[]` via the existing `parse_event_groups`.

**Files:**
- Modify: `data/discovery.py`
- Test: `tests/data/test_discovery.py`

**Interfaces:**
- Consumes: `classify_event` (Task 4), `parse_event_groups` (`data/gamma.py`), `DiscoveryManifest` (Task 3).
- Produces: `build_manifest(topic: str, tag: str, events: Sequence[GammaEvent], discovered_at: datetime) -> DiscoveryManifest`.

- [ ] **Step 1: Write the failing test**

Add to `tests/data/test_discovery.py`:

```python
from datetime import UTC, datetime

from data.discovery import build_manifest


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


def test_build_manifest_groups_reference_known_markets() -> None:
    manifest = build_manifest(
        "fifa-2026", "world-cup", _fifa_events(), datetime(2026, 6, 27, tzinfo=UTC)
    )
    known = {m.market_id for m in manifest.markets}
    for group in manifest.groups:
        for market_id in group.market_ids:
            assert market_id in known
    # the standalone prop (single non-negRisk leg) forms no group
    assert all(g.group_id != "70004" for g in manifest.groups)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/data/test_discovery.py -k build_manifest -v`
Expected: FAIL (`ImportError` — `build_manifest` not defined).

- [ ] **Step 3: Write minimal implementation**

Add to `data/discovery.py` (extend imports):

```python
from collections.abc import Sequence
from datetime import datetime

from data.events import DiscoveredMarket, DiscoveryManifest
from data.gamma import parse_event_groups


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
    groups = tuple(g for event in events for g in parse_event_groups(event))
    return DiscoveryManifest(
        topic=topic,
        tag=tag,
        discovered_at=discovered_at,
        markets=tuple(markets),
        groups=groups,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/data/test_discovery.py -v`
Expected: PASS (all discovery tests). Note: importing `parse_event_groups` from `data.gamma` into `data.discovery` is safe — no import cycle (`gamma` does not import `discovery`).

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check . && uv run mypy .
git add data/discovery.py tests/data/test_discovery.py
git commit -m "feat: build_manifest aggregates classified markets + negRisk groups"
```

---

## Task 6: Persistence — `write_manifest` / `load_latest_manifest` + gitignore

Append-only JSON snapshots under `data/discovery/`, plus a loader for the newest.

**Files:**
- Modify: `data/discovery.py`
- Modify: `.gitignore`
- Test: `tests/data/test_discovery.py`

**Interfaces:**
- Consumes: `DiscoveryManifest` (Task 3).
- Produces:
  - `write_manifest(manifest: DiscoveryManifest, directory: Path) -> Path`
  - `load_latest_manifest(directory: Path) -> DiscoveryManifest`
  - `DISCOVERY_DIR: Path` (module constant = `Path("data/discovery")`).

- [ ] **Step 1: Write the failing test**

Add to `tests/data/test_discovery.py`:

```python
from data.discovery import load_latest_manifest, write_manifest


def _manifest(stamp: datetime) -> "DiscoveryManifest":
    return build_manifest("fifa-2026", "world-cup", _fifa_events(), stamp)


def test_write_then_load_latest_roundtrips(tmp_path: Path) -> None:
    early = _manifest(datetime(2026, 6, 27, 9, 0, tzinfo=UTC))
    late = _manifest(datetime(2026, 6, 27, 18, 0, tzinfo=UTC))
    write_manifest(early, tmp_path)
    write_manifest(late, tmp_path)
    loaded = load_latest_manifest(tmp_path)
    assert loaded.discovered_at == late.discovered_at
    assert [m.market_id for m in loaded.markets] == [
        m.market_id for m in late.markets
    ]


def test_write_manifest_is_append_only(tmp_path: Path) -> None:
    write_manifest(_manifest(datetime(2026, 6, 27, 9, 0, tzinfo=UTC)), tmp_path)
    write_manifest(_manifest(datetime(2026, 6, 27, 10, 0, tzinfo=UTC)), tmp_path)
    assert len(list(tmp_path.glob("*.json"))) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/data/test_discovery.py -k manifest_is_append or roundtrips -v`
Expected: FAIL (`ImportError` — functions not defined).

- [ ] **Step 3: Write minimal implementation**

Add to `data/discovery.py` (extend imports with `from pathlib import Path`):

```python
# Top-level var/ keeps run-artifacts out of the importable data/ package and
# avoids a name collision with the data/discovery.py module.
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/data/test_discovery.py -v`
Expected: PASS. Filenames sort chronologically because the `%Y%m%dT%H%M%SZ` stamp is lexicographically ordered.

- [ ] **Step 5: Ignore generated snapshots**

Add to `.gitignore`:

```
var/
```

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run ruff check . && uv run mypy .
git add data/discovery.py tests/data/test_discovery.py .gitignore
git commit -m "feat: persist + load discovery manifest snapshots"
```

---

## Task 7: `discover_fifa_markets` orchestrator

The only network-touching function: fetch tagged events, build the manifest, raise on an implausibly small result, persist, return.

**Files:**
- Modify: `data/discovery.py`
- Test: `tests/data/test_discovery.py`

**Interfaces:**
- Consumes: `GammaClient.fetch_events(params=...)` (Task 2), `build_manifest` (Task 5), `write_manifest` (Task 6).
- Produces:
  - `async discover_fifa_markets(client: GammaClient, *, tag: str, tag_param: str = "tag", topic: str = "fifa-2026", directory: Path = DISCOVERY_DIR, now: datetime | None = None, min_markets: int = 1) -> DiscoveryManifest`
  - `class DiscoveryError(RuntimeError)`

`tag` is the filter *value* (slug or id); `tag_param` is the Gamma query *key*
(`"tag"` by default, set to whatever Task 0 confirms — e.g. `series_id`). Keeping
them separate stays coherent even if the real key turns out to be a numeric id.

- [ ] **Step 1: Write the failing test**

Add to `tests/data/test_discovery.py`:

```python
import asyncio

import httpx
import pytest

from data.discovery import DiscoveryError, discover_fifa_markets
from data.gamma import GammaClient


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
                await discover_fifa_markets(
                    client, tag="bogus-tag", directory=tmp_path
                )

    asyncio.run(_run())


def test_discover_raises_below_floor(tmp_path: Path) -> None:
    # the FIFA fixture yields 10 markets; a high floor must still trip the guard
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
```

> Operational note: callers should pass a realistic `min_markets` floor (dozens,
> for a full World Cup) so a mistagged-but-nonempty result still trips the guard.
> The default of 1 only catches a truly empty universe.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/data/test_discovery.py -k discover -v`
Expected: FAIL (`ImportError` — `discover_fifa_markets`/`DiscoveryError` not defined).

- [ ] **Step 3: Write minimal implementation**

Add to `data/discovery.py` (extend imports with `from datetime import UTC, datetime` and `from data.gamma import GammaClient, parse_event_groups`):

```python
class DiscoveryError(RuntimeError):
    """Raised when discovery returns an implausibly small market set."""


async def discover_fifa_markets(
    client: GammaClient,
    *,
    tag: str,
    tag_param: str = "tag",  # Gamma /events filter key; confirm via Task 0 probe
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/data/test_discovery.py -v`
Expected: PASS (all discovery tests).

- [ ] **Step 5: Full gate + commit**

```bash
uv run pytest && uv run ruff check . && uv run mypy .
git add data/discovery.py tests/data/test_discovery.py
git commit -m "feat: discover_fifa_markets orchestrator with empty-result guard"
```

---

## Done criteria

- `uv run pytest`, `uv run ruff check .`, `uv run mypy .` all green.
- `discover_fifa_markets` produces a classified, deduped, persisted manifest from recorded fixtures with no network in tests.
- Task 0's probe findings are recorded in the spec, and `TAG_PARAM` / the `match_moneyline` rule reflect them.
- Out of scope (later steps): outcome labelling, price collection, reference matching, the walk-forward runner.
