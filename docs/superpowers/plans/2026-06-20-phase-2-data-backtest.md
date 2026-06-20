# Phase 2 — Data & backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first IO edge (full async Gamma/CLOB clients, tested entirely offline) and a pure walk-forward backtest harness that makes look-ahead bias structurally impossible and replays deterministically.

**Architecture:** Network lives only in `data/` (async clients behind injected, mockable transports; pure parsers turn raw payloads into canonical `MarketEvent`/`Quote` records). `backtest/` is pure replay over those records: a chronology-validating feed, an as-of `MarketView` that raises on any future query, a `replay` engine that drives a `Strategy` through the Phase-1 core and accounts P&L via `core.metrics`, plus an index-based walk-forward splitter and a thin report.

**Tech Stack:** Python 3.12, pydantic v2, httpx (async REST + built-in `MockTransport` for tests), websockets (CLOB WS, injected fake in tests), polars (bulk history), numpy/scikit-learn (Phase 1 core, reused), pytest + hypothesis, ruff, mypy (strict). Async tests run via `asyncio.run(...)` inside sync test functions — **no new dev dependency**. Toolchain runs inside `nix develop` via `uv`.

**Spec:** `docs/superpowers/specs/2026-06-20-phase-2-data-backtest-design.md`

## Global Constraints

These apply to every task:

- **TDD, red→green.** Write the failing test first; no task is done until its tests pass **and** `uv run ruff check`, `uv run ruff format --check`, `uv run mypy` are clean. Never advance with a red gate.
- **No live network in tests, ever.** Every REST test uses `httpx.MockTransport`; every WS test injects a fake async connection. An autouse `tests/conftest.py` fixture blocks DNS so an accidental real connection fails loudly. Parsers are tested against committed JSON fixtures.
- **Pure core unchanged.** Reuse `core/` (`core.decision.evaluate`, `core.metrics`, `core.models`) as-is. `backtest/` must not import `httpx`/`websockets`/any `data/` client — only `data.events` records and `data.reference`.
- **Numeric split (from Phase 1):** `Decimal` for prices/quotes/cash/P&L; `float` only for statistical math. Convert explicitly via `Decimal(str(x))`. Timestamps are timezone-aware UTC `datetime`.
- **Look-ahead is structural:** the as-of guard (`ts > as_of ⇒ raise LookAheadError`) lives in `backtest/view.py` and `backtest/feed.py`. The backtest may only expose data with `ts ≤ as_of`.
- **Secrets from env only.** CLOB auth fields come from `os.environ`; never in code, fixtures, logs, or commits. The secret is never placed in a header in this phase (request signing is Phase 5).
- **New runtime deps:** `httpx`, `websockets`, `polars` only. No new dev deps. After any dependency change run `uv lock` before commit or CI `--frozen` fails.
- **Commands run in the devshell:** prefix with `nix develop --command` (e.g. `nix develop --command uv run pytest`).
- **Attribution:** commit messages carry NO `Co-Authored-By` / "Generated with" trailers (per `AGENTS.md`).

## Git & commit protocol (read before Task 1)

- Work is on branch `phase-2-data-backtest` (already created from `main`). All Phase 2 commits land here. The spec + this plan are committed first (see below).
- **Commit only on explicit user instruction.** Commit steps are written out; the executor runs them only once authorized.
- Stage files **explicitly by path** — never `git add -A` / `git add .`.
- Do not push unless asked.

---

### Task 0: Commit the spec and this plan

**Files:**
- Commit: `docs/superpowers/specs/2026-06-20-phase-2-data-backtest-design.md`
- Commit: `docs/superpowers/plans/2026-06-20-phase-2-data-backtest.md`

- [ ] **Step 1: Commit the planning docs**

```bash
git add docs/superpowers/specs/2026-06-20-phase-2-data-backtest-design.md docs/superpowers/plans/2026-06-20-phase-2-data-backtest.md
git commit -m "docs: phase 2 spec + implementation plan"
```

---

### Task 1: Add httpx + websockets + polars deps

**Files:**
- Modify: `pyproject.toml` (`[project] dependencies`)
- Modify (generated): `uv.lock`

**Interfaces:**
- Produces: `httpx`, `websockets`, `polars` importable in the venv. All later tasks rely on this.

- [ ] **Step 1: Add the dependencies to `pyproject.toml`**

Change the `dependencies` array under `[project]` from:
```toml
dependencies = [
    "pydantic>=2",
    "numpy>=2",
    "scikit-learn>=1.5",
]
```
to:
```toml
dependencies = [
    "pydantic>=2",
    "numpy>=2",
    "scikit-learn>=1.5",
    "httpx>=0.27",
    "websockets>=13",
    "polars>=1",
]
```

- [ ] **Step 2: Lock and sync**

Run:
```bash
nix develop --command uv lock
nix develop --command uv sync
```
Expected: `uv.lock` updated with `httpx` (+ `httpcore`, `h11`, `anyio`, `sniffio`, `certifi`, `idna`), `websockets`, `polars`; `.venv` populated.

- [ ] **Step 3: Verify imports resolve**

Run: `nix develop --command uv run python -c "import httpx, websockets, polars; print(httpx.__version__, websockets.__version__, polars.__version__)"`
Expected: three version strings, no `ModuleNotFoundError`.

- [ ] **Step 4: Confirm gates still green**

Run: `nix develop --command bash -c 'uv run ruff check && uv run mypy && uv run pytest'`
Expected: the Phase 1 suite still passes; mypy clean (all three deps ship `py.typed`, so no overrides expected).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add httpx + websockets + polars for the data edge"
```

---

### Task 2: Raw API payload models + network guard (`data/payloads.py`)

**Files:**
- Create: `data/payloads.py`
- Create: `tests/data/__init__.py` (empty)
- Create: `tests/conftest.py`
- Create: `tests/data/test_payloads.py`

**Interfaces:**
- Produces (consumed by parsers in Tasks 5–6):
  - `GammaMarket`: `id: str`, `question: str`, `clobTokenIds: list[str]`, `tickSize: Decimal = Decimal("0.01")`, `active: bool = True`, `closed: bool = False`.
  - `GammaPricePoint`: `t: int`, `p: float`. `GammaPriceHistory`: `history: list[GammaPricePoint]`.
  - `ClobBookLevel`: `price: Decimal`, `size: Decimal`. `ClobBook`: `market: str`, `asks: list[ClobBookLevel]`, `bids: list[ClobBookLevel]`, `timestamp: int | None = None`.
  - `ClobPricePoint`: `t: int`, `p: Decimal`. `ClobPriceHistory`: `history: list[ClobPricePoint]`.
  - All `frozen=True`, `extra="ignore"` so unknown wire fields are tolerated; missing required fields raise `ValidationError`.

- [ ] **Step 1: Write the failing tests**

`tests/data/__init__.py`: empty file.

`tests/conftest.py`:
```python
"""Global test guards. Block real network so no unit test can reach a socket."""

import socket
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def _blocked(*args: object, **kwargs: object) -> None:
        raise RuntimeError("network access is disabled in tests")

    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    yield
```

`tests/data/test_payloads.py`:
```python
"""Raw API payload models: tolerant parsing, required-field validation."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from data.payloads import (
    ClobBook,
    ClobPriceHistory,
    GammaMarket,
    GammaPriceHistory,
)


def test_gamma_market_parses_and_ignores_extra() -> None:
    m = GammaMarket.model_validate(
        {
            "id": "0xabc",
            "question": "Will Team A win?",
            "clobTokenIds": ["111", "222"],
            "tickSize": "0.01",
            "active": True,
            "closed": False,
            "unknownField": "ignored",
        }
    )
    assert m.id == "0xabc"
    assert m.clobTokenIds == ["111", "222"]
    assert m.tickSize == Decimal("0.01")


def test_gamma_market_requires_id() -> None:
    with pytest.raises(ValidationError):
        GammaMarket.model_validate({"question": "q", "clobTokenIds": []})


def test_gamma_price_history_parses() -> None:
    h = GammaPriceHistory.model_validate(
        {"history": [{"t": 1718800000, "p": 0.45}, {"t": 1718800600, "p": 0.47}]}
    )
    assert len(h.history) == 2
    assert h.history[0].p == 0.45


def test_clob_book_parses_levels() -> None:
    b = ClobBook.model_validate(
        {
            "market": "0xabc",
            "timestamp": 1718800000000,
            "asks": [{"price": "0.53", "size": "100"}],
            "bids": [{"price": "0.51", "size": "150"}],
        }
    )
    assert b.market == "0xabc"
    assert b.asks[0].price == Decimal("0.53")
    assert b.bids[0].size == Decimal("150")


def test_clob_price_history_parses_decimal() -> None:
    h = ClobPriceHistory.model_validate(
        {"history": [{"t": 1718800000, "p": "0.50"}]}
    )
    assert h.history[0].p == Decimal("0.50")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix develop --command uv run pytest tests/data/test_payloads.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'data.payloads'`.

- [ ] **Step 3: Implement `data/payloads.py`**

```python
"""Raw Polymarket API payload models (the wire shapes we consume).

Tolerant of unknown fields (extra="ignore"); missing required fields raise.
These are parsed into canonical records in data/gamma.py and data/clob.py.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class GammaPricePoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    t: int
    p: float


class GammaPriceHistory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    history: list[GammaPricePoint]


class GammaMarket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    question: str
    clobTokenIds: list[str] = Field(default_factory=list)  # noqa: N815
    tickSize: Decimal = Decimal("0.01")  # noqa: N815
    active: bool = True
    closed: bool = False


class ClobBookLevel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    price: Decimal
    size: Decimal


class ClobBook(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    market: str
    asks: list[ClobBookLevel] = Field(default_factory=list)
    bids: list[ClobBookLevel] = Field(default_factory=list)
    timestamp: int | None = None


class ClobPricePoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    t: int
    p: Decimal


class ClobPriceHistory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    history: list[ClobPricePoint]
```

Note: `clobTokenIds` and `tickSize` keep the wire's camelCase names; the `# noqa: N815` silences ruff's mixedCase-field warning for these two API-mirroring fields.

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix develop --command uv run pytest tests/data/test_payloads.py -v`
Expected: all `PASSED`.

- [ ] **Step 5: Run the gates**

Run: `nix develop --command bash -c 'uv run ruff check && uv run ruff format --check && uv run mypy'`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add data/payloads.py tests/data/__init__.py tests/conftest.py tests/data/test_payloads.py
git commit -m "feat(data): raw API payload models + offline network guard"
```

---

### Task 3: Canonical records + polars history (`data/events.py`, `data/history.py`)

**Files:**
- Create: `data/events.py`
- Create: `data/history.py`
- Create: `tests/data/test_events.py`
- Create: `tests/data/test_history.py`

**Interfaces:**
- Produces:
  - `Market`: `market_id: str`, `question: str`, `token_ids: tuple[str, ...]`, `tick_size: Decimal` (`> 0`), `active: bool = True`.
  - `Quote`: `market_id: str`, `ts: datetime` (tz-aware), `price: Decimal` in `(0, 1)`, `bid/ask/size: Decimal | None = None`.
  - `MarketEvent`: `ts: datetime` (tz-aware), `market_id: str`, `quote: Quote`.
  - `event_from_quote(quote: Quote) -> MarketEvent`.
  - `quotes_to_frame(quotes: Sequence[Quote]) -> pl.DataFrame`; `frame_to_events(df: pl.DataFrame) -> list[MarketEvent]` (sorted by `ts`).

- [ ] **Step 1: Write the failing tests**

`tests/data/test_events.py`:
```python
"""Canonical records: tz-aware timestamps, price bounds, event helper."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from data.events import Quote, event_from_quote


def _quote() -> Quote:
    return Quote(
        market_id="m1",
        ts=datetime(2024, 6, 1, 12, 0, tzinfo=UTC),
        price=Decimal("0.45"),
    )


def test_quote_requires_tz_aware_ts() -> None:
    with pytest.raises(ValidationError):
        Quote(market_id="m1", ts=datetime(2024, 6, 1, 12, 0), price=Decimal("0.45"))


def test_quote_rejects_out_of_range_price() -> None:
    with pytest.raises(ValidationError):
        Quote(
            market_id="m1",
            ts=datetime(2024, 6, 1, tzinfo=UTC),
            price=Decimal("1.0"),
        )


def test_event_from_quote_copies_ts_and_market() -> None:
    q = _quote()
    e = event_from_quote(q)
    assert e.ts == q.ts
    assert e.market_id == q.market_id
    assert e.quote == q


def test_models_are_frozen() -> None:
    q = _quote()
    with pytest.raises(ValidationError):
        q.price = Decimal("0.5")
```

`tests/data/test_history.py`:
```python
"""polars bulk-history conversions: quotes <-> frame <-> ordered events."""

from datetime import UTC, datetime
from decimal import Decimal

from data.events import Quote
from data.history import frame_to_events, quotes_to_frame


def _quotes() -> list[Quote]:
    return [
        Quote(market_id="m1", ts=datetime(2024, 6, 1, 0, 2, tzinfo=UTC), price=Decimal("0.47")),
        Quote(market_id="m1", ts=datetime(2024, 6, 1, 0, 1, tzinfo=UTC), price=Decimal("0.45")),
    ]


def test_frame_roundtrip_sorts_by_ts() -> None:
    df = quotes_to_frame(_quotes())
    events = frame_to_events(df)
    # frame_to_events must return chronological order regardless of input order.
    assert [e.ts.minute for e in events] == [1, 2]
    assert [e.quote.price for e in events] == [Decimal("0.45"), Decimal("0.47")]


def test_frame_has_expected_columns() -> None:
    df = quotes_to_frame(_quotes())
    assert set(df.columns) == {"market_id", "ts", "price"}
    assert df.height == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix develop --command uv run pytest tests/data/test_events.py tests/data/test_history.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'data.events'`.

- [ ] **Step 3: Implement `data/events.py`**

```python
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
```

- [ ] **Step 4: Implement `data/history.py`**

```python
"""Bulk historical loading via polars: quotes <-> DataFrame <-> ordered events.

The single place polars is used. float prices in the frame are for bulk
analytics; conversion back to Decimal happens when building events.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import polars as pl

from data.events import MarketEvent, Quote, event_from_quote


def quotes_to_frame(quotes: Sequence[Quote]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "market_id": [q.market_id for q in quotes],
            "ts": [q.ts for q in quotes],
            "price": [float(q.price) for q in quotes],
        }
    )


def frame_to_events(df: pl.DataFrame) -> list[MarketEvent]:
    events: list[MarketEvent] = []
    for row in df.sort("ts").iter_rows(named=True):
        quote = Quote(
            market_id=row["market_id"],
            ts=row["ts"],
            price=Decimal(str(row["price"])),
        )
        events.append(event_from_quote(quote))
    return events
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `nix develop --command uv run pytest tests/data/test_events.py tests/data/test_history.py -v`
Expected: all `PASSED`.

- [ ] **Step 6: Run the gates**

Run: `nix develop --command bash -c 'uv run ruff check && uv run ruff format --check && uv run mypy'`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add data/events.py data/history.py tests/data/test_events.py tests/data/test_history.py
git commit -m "feat(data): canonical event records + polars history conversions"
```

---

### Task 4: Shared HTTP helpers (`data/http.py`)

**Files:**
- Create: `data/http.py`
- Create: `tests/data/test_http.py`

**Interfaces:**
- Produces (consumed by both clients in Tasks 5–6):
  - `class RateLimiter`: `__init__(self, max_concurrency: int = 8)`; `async def acquire`, `def release`, async context manager. Bounds concurrent in-flight requests.
  - `async def get_json(client: httpx.AsyncClient, url: str, params: Mapping[str, Any] | None = None, *, limiter: RateLimiter, max_retries: int = 3, retry_backoff: float = 0.5, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> Any` — GET with bounded retry on transient/5xx/429 errors, returns parsed JSON.

- [ ] **Step 1: Write the failing tests**

`tests/data/test_http.py`:
```python
"""Shared HTTP helpers: rate-limit bounding and bounded retry."""

import asyncio

import httpx
import pytest

from data.http import RateLimiter, get_json


def test_rate_limiter_bounds_concurrency() -> None:
    async def _run() -> None:
        rl = RateLimiter(max_concurrency=1)
        await rl.acquire()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(rl.acquire(), timeout=0.05)
        rl.release()

    asyncio.run(_run())


def test_get_json_retries_transient_then_succeeds() -> None:
    async def _run() -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.ConnectError("boom", request=request)
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(base_url="http://t", transport=transport) as client:
            data = await get_json(
                client, "/x", limiter=RateLimiter(2), retry_backoff=0.0
            )
        assert data == {"ok": True}
        assert calls["n"] == 3

    asyncio.run(_run())


def test_get_json_does_not_retry_4xx() -> None:
    async def _run() -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(base_url="http://t", transport=transport) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await get_json(client, "/x", limiter=RateLimiter(2), retry_backoff=0.0)
        assert calls["n"] == 1

    asyncio.run(_run())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix develop --command uv run pytest tests/data/test_http.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'data.http'`.

- [ ] **Step 3: Implement `data/http.py`**

```python
"""Shared HTTP helpers for the data adapters: rate limiting and bounded retry."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Self

import httpx


class RateLimiter:
    """Bounds the number of concurrent in-flight requests."""

    def __init__(self, max_concurrency: int = 8) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        self._sem = asyncio.Semaphore(max_concurrency)

    async def acquire(self) -> None:
        await self._sem.acquire()

    def release(self) -> None:
        self._sem.release()

    async def __aenter__(self) -> Self:
        await self.acquire()
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.release()


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    params: Mapping[str, Any] | None = None,
    *,
    limiter: RateLimiter,
    max_retries: int = 3,
    retry_backoff: float = 0.5,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Any:
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            async with limiter:
                resp = await client.get(url, params=dict(params or {}))
            resp.raise_for_status()
            return resp.json()
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            if attempt < max_retries and _is_retryable(exc):
                await sleep(retry_backoff * (2**attempt))
                continue
            raise
    assert last_exc is not None  # unreachable: loop either returns or raises
    raise last_exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix develop --command uv run pytest tests/data/test_http.py -v`
Expected: all `PASSED`.

- [ ] **Step 5: Run the gates**

Run: `nix develop --command bash -c 'uv run ruff check && uv run ruff format --check && uv run mypy'`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add data/http.py tests/data/test_http.py
git commit -m "feat(data): shared rate-limiter + bounded-retry get_json"
```

---

### Task 5: Gamma adapter (`data/gamma.py`)

**Files:**
- Create: `data/gamma.py`
- Create: `tests/fixtures/gamma/markets.json`
- Create: `tests/fixtures/gamma/prices_history.json`
- Create: `tests/data/test_gamma.py`

**Interfaces:**
- Consumes: `GammaMarket`, `GammaPriceHistory` (Task 2); `Market`, `Quote` (Task 3); `RateLimiter`, `get_json` (Task 4).
- Produces:
  - `parse_market(raw: GammaMarket) -> Market`.
  - `parse_price_history(market_id: str, raw: GammaPriceHistory) -> list[Quote]`.
  - `class GammaClient`: `__init__(self, *, transport: httpx.AsyncBaseTransport | None = None, base_url: str = "https://gamma-api.polymarket.com", limiter: RateLimiter | None = None, max_retries: int = 3, retry_backoff: float = 0.5, headers: Mapping[str, str] | None = None)`; async context manager; `async def fetch_markets(self, *, limit: int = 100) -> list[Market]` (offset pagination); `async def fetch_price_history(self, market_id: str) -> list[Quote]`; `async def aclose(self) -> None`.

- [ ] **Step 1: Create the fixtures**

`tests/fixtures/gamma/markets.json`:
```json
[
  {"id": "m0", "question": "Will Team A win?", "clobTokenIds": ["111", "222"], "tickSize": "0.01", "active": true, "closed": false},
  {"id": "m1", "question": "Will Team B win?", "clobTokenIds": ["333", "444"], "tickSize": "0.01", "active": true, "closed": false},
  {"id": "m2", "question": "Will the match draw?", "clobTokenIds": ["555", "666"], "tickSize": "0.01", "active": true, "closed": false}
]
```

`tests/fixtures/gamma/prices_history.json`:
```json
{"history": [{"t": 1718800000, "p": 0.45}, {"t": 1718800600, "p": 0.47}, {"t": 1718801200, "p": 0.52}]}
```

- [ ] **Step 2: Write the failing tests**

`tests/data/test_gamma.py`:
```python
"""Gamma adapter: pure parsers (fixtures) + async client over MockTransport."""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from data.events import Market, Quote
from data.gamma import GammaClient, parse_market, parse_price_history
from data.payloads import GammaMarket, GammaPriceHistory

_FIX = Path(__file__).parent.parent / "fixtures" / "gamma"


def test_parse_market_maps_fields() -> None:
    raw = GammaMarket.model_validate(json.loads((_FIX / "markets.json").read_text())[0])
    market = parse_market(raw)
    assert isinstance(market, Market)
    assert market.market_id == "m0"
    assert market.token_ids == ("111", "222")
    assert market.tick_size == Decimal("0.01")
    assert market.active is True


def test_parse_market_inactive_when_closed() -> None:
    raw = GammaMarket(
        id="x", question="q", clobTokenIds=["1"], active=True, closed=True
    )
    assert parse_market(raw).active is False


def test_parse_price_history_builds_quotes() -> None:
    raw = GammaPriceHistory.model_validate(
        json.loads((_FIX / "prices_history.json").read_text())
    )
    quotes = parse_price_history("m0", raw)
    assert [q.price for q in quotes] == [Decimal("0.45"), Decimal("0.47"), Decimal("0.52")]
    assert quotes[0].ts == datetime.fromtimestamp(1718800000, tz=UTC)
    assert all(isinstance(q, Quote) for q in quotes)


def test_fetch_markets_paginates() -> None:
    async def _run() -> None:
        all_markets = json.loads((_FIX / "markets.json").read_text())

        def handler(request: httpx.Request) -> httpx.Response:
            offset = int(request.url.params["offset"])
            limit = int(request.url.params["limit"])
            return httpx.Response(200, json=all_markets[offset : offset + limit])

        transport = httpx.MockTransport(handler)
        async with GammaClient(
            transport=transport, base_url="http://t", retry_backoff=0.0
        ) as client:
            markets = await client.fetch_markets(limit=2)
        assert [m.market_id for m in markets] == ["m0", "m1", "m2"]

    asyncio.run(_run())


def test_fetch_price_history_returns_quotes() -> None:
    async def _run() -> None:
        payload = json.loads((_FIX / "prices_history.json").read_text())

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["market"] == "m0"
            return httpx.Response(200, json=payload)

        transport = httpx.MockTransport(handler)
        async with GammaClient(
            transport=transport, base_url="http://t", retry_backoff=0.0
        ) as client:
            quotes = await client.fetch_price_history("m0")
        assert len(quotes) == 3
        assert quotes[-1].price == Decimal("0.52")

    asyncio.run(_run())
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `nix develop --command uv run pytest tests/data/test_gamma.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'data.gamma'`.

- [ ] **Step 4: Implement `data/gamma.py`**

```python
"""Gamma REST adapter: pure parsers + a full async client (offset pagination)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Self

import httpx

from data.events import Market, Quote
from data.http import RateLimiter, get_json
from data.payloads import GammaMarket, GammaPriceHistory

GAMMA_BASE_URL = "https://gamma-api.polymarket.com"


def parse_market(raw: GammaMarket) -> Market:
    return Market(
        market_id=raw.id,
        question=raw.question,
        token_ids=tuple(raw.clobTokenIds),
        tick_size=raw.tickSize,
        active=raw.active and not raw.closed,
    )


def parse_price_history(market_id: str, raw: GammaPriceHistory) -> list[Quote]:
    return [
        Quote(
            market_id=market_id,
            ts=datetime.fromtimestamp(point.t, tz=UTC),
            price=Decimal(str(point.p)),
        )
        for point in raw.history
    ]


class GammaClient:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = GAMMA_BASE_URL,
        limiter: RateLimiter | None = None,
        max_retries: int = 3,
        retry_backoff: float = 0.5,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, transport=transport, headers=dict(headers or {})
        )
        self._limiter = limiter or RateLimiter()
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_markets(self, *, limit: int = 100) -> list[Market]:
        markets: list[Market] = []
        offset = 0
        while True:
            raw = await get_json(
                self._client,
                "/markets",
                {"limit": limit, "offset": offset},
                limiter=self._limiter,
                max_retries=self._max_retries,
                retry_backoff=self._retry_backoff,
            )
            page = [parse_market(GammaMarket.model_validate(m)) for m in raw]
            markets.extend(page)
            if len(page) < limit:
                break
            offset += limit
        return markets

    async def fetch_price_history(self, market_id: str) -> list[Quote]:
        raw = await get_json(
            self._client,
            "/prices-history",
            {"market": market_id},
            limiter=self._limiter,
            max_retries=self._max_retries,
            retry_backoff=self._retry_backoff,
        )
        return parse_price_history(market_id, GammaPriceHistory.model_validate(raw))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `nix develop --command uv run pytest tests/data/test_gamma.py -v`
Expected: all `PASSED`.

- [ ] **Step 6: Run the gates**

Run: `nix develop --command bash -c 'uv run ruff check && uv run ruff format --check && uv run mypy'`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add data/gamma.py tests/fixtures/gamma tests/data/test_gamma.py
git commit -m "feat(data): Gamma adapter — parsers + paginated async client"
```

---

### Task 6: CLOB adapter (`data/clob.py`)

**Files:**
- Create: `data/clob.py`
- Create: `tests/fixtures/clob/book.json`
- Create: `tests/fixtures/clob/prices_history.json`
- Create: `tests/data/test_clob.py`

**Interfaces:**
- Consumes: `ClobBook`, `ClobPriceHistory` (Task 2); `Quote` (Task 3); `RateLimiter`, `get_json` (Task 4).
- Produces:
  - `parse_book(raw: ClobBook) -> Quote` (mid of best bid/ask; `bid`/`ask`/`size` populated; requires `timestamp`).
  - `parse_price_history(token_id: str, raw: ClobPriceHistory) -> list[Quote]`.
  - `parse_ws_message(msg: Mapping[str, Any]) -> Quote | None` (book messages → `Quote`; others → `None`).
  - `class ClobAuth`: frozen dataclass `api_key/secret/passphrase: str`; `classmethod from_env() -> ClobAuth | None`.
  - `class ClobClient`: REST client, same shape as `GammaClient` plus `auth: ClobAuth | None = None`; `async def fetch_book(self, token_id: str) -> Quote`; `async def fetch_price_history(self, token_id: str) -> list[Quote]`.
  - `class ClobWsClient`: `__init__(self, *, ws_connect: Callable[..., Any] | None = None, url: str = ...)`; `async def stream(self, market_ids: Sequence[str]) -> AsyncIterator[Quote]`.

- [ ] **Step 1: Create the fixtures**

`tests/fixtures/clob/book.json`:
```json
{"market": "0xabc", "timestamp": 1718800000000, "asks": [{"price": "0.54", "size": "200"}, {"price": "0.53", "size": "100"}], "bids": [{"price": "0.51", "size": "150"}, {"price": "0.50", "size": "300"}]}
```

`tests/fixtures/clob/prices_history.json`:
```json
{"history": [{"t": 1718800000, "p": "0.50"}, {"t": 1718800600, "p": "0.55"}]}
```

- [ ] **Step 2: Write the failing tests**

`tests/data/test_clob.py`:
```python
"""CLOB adapter: parsers (fixtures) + REST client + WS client (fake connection)."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from data.clob import (
    ClobAuth,
    ClobClient,
    ClobWsClient,
    parse_book,
    parse_price_history,
    parse_ws_message,
)
from data.payloads import ClobBook, ClobPriceHistory

_FIX = Path(__file__).parent.parent / "fixtures" / "clob"


def test_parse_book_uses_mid_and_best_levels() -> None:
    raw = ClobBook.model_validate(json.loads((_FIX / "book.json").read_text()))
    quote = parse_book(raw)
    assert quote.market_id == "0xabc"
    assert quote.bid == Decimal("0.51")
    assert quote.ask == Decimal("0.53")
    assert quote.price == Decimal("0.52")  # mid of 0.51 / 0.53
    assert quote.ts == datetime.fromtimestamp(1718800000, tz=UTC)


def test_parse_price_history_decimal_quotes() -> None:
    raw = ClobPriceHistory.model_validate(
        json.loads((_FIX / "prices_history.json").read_text())
    )
    quotes = parse_price_history("111", raw)
    assert [q.price for q in quotes] == [Decimal("0.50"), Decimal("0.55")]
    assert quotes[0].market_id == "111"


def test_parse_ws_message_book_and_other() -> None:
    book_msg = json.loads((_FIX / "book.json").read_text())
    book_msg["event_type"] = "book"
    quote = parse_ws_message(book_msg)
    assert quote is not None and quote.price == Decimal("0.52")
    assert parse_ws_message({"event_type": "ack"}) is None


def test_clob_auth_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOB_API_KEY", "k")
    monkeypatch.setenv("CLOB_API_SECRET", "s")
    monkeypatch.setenv("CLOB_API_PASSPHRASE", "p")
    auth = ClobAuth.from_env()
    assert auth is not None and auth.api_key == "k"


def test_clob_auth_from_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLOB_API_KEY", raising=False)
    monkeypatch.delenv("CLOB_API_SECRET", raising=False)
    monkeypatch.delenv("CLOB_API_PASSPHRASE", raising=False)
    assert ClobAuth.from_env() is None


def test_fetch_book_over_mock_transport() -> None:
    async def _run() -> None:
        payload = json.loads((_FIX / "book.json").read_text())

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["token_id"] == "111"
            return httpx.Response(200, json=payload)

        transport = httpx.MockTransport(handler)
        async with ClobClient(
            transport=transport, base_url="http://t", retry_backoff=0.0
        ) as client:
            quote = await client.fetch_book("111")
        assert quote.price == Decimal("0.52")

    asyncio.run(_run())


class _FakeWs:
    def __init__(self, frames: list[str]) -> None:
        self._frames = frames
        self.sent: list[str] = []

    async def __aenter__(self) -> "_FakeWs":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def send(self, message: str) -> None:
        self.sent.append(message)

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[str]:
        for frame in self._frames:
            yield frame


def test_ws_stream_yields_quotes() -> None:
    async def _run() -> None:
        book_msg = json.loads((_FIX / "book.json").read_text())
        book_msg["event_type"] = "book"
        frames = [json.dumps(book_msg), json.dumps({"event_type": "ack"})]
        client = ClobWsClient(ws_connect=lambda url: _FakeWs(frames))
        quotes = [q async for q in client.stream(["0xabc"])]
        assert len(quotes) == 1
        assert quotes[0].price == Decimal("0.52")

    asyncio.run(_run())
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `nix develop --command uv run pytest tests/data/test_clob.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'data.clob'`.

- [ ] **Step 4: Implement `data/clob.py`**

```python
"""CLOB adapter: pure parsers + async REST client + WS subscription client.

Auth fields come from env only; the secret is never sent as a header here —
full L2 request signing is Phase 5. Public market-data reads need no auth.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Self

import httpx
import websockets

from data.events import Quote
from data.http import RateLimiter, get_json
from data.payloads import ClobBook, ClobPriceHistory

CLOB_BASE_URL = "https://clob.polymarket.com"
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def parse_book(raw: ClobBook) -> Quote:
    if not raw.asks or not raw.bids:
        raise ValueError("book is missing bid/ask levels")
    if raw.timestamp is None:
        raise ValueError("book is missing a timestamp")
    best_ask = min(raw.asks, key=lambda level: level.price)
    best_bid = max(raw.bids, key=lambda level: level.price)
    mid = (best_ask.price + best_bid.price) / 2
    return Quote(
        market_id=raw.market,
        ts=datetime.fromtimestamp(raw.timestamp / 1000, tz=UTC),
        price=mid,
        bid=best_bid.price,
        ask=best_ask.price,
        size=best_ask.size,
    )


def parse_price_history(token_id: str, raw: ClobPriceHistory) -> list[Quote]:
    return [
        Quote(
            market_id=token_id,
            ts=datetime.fromtimestamp(point.t, tz=UTC),
            price=point.p,
        )
        for point in raw.history
    ]


def parse_ws_message(msg: Mapping[str, Any]) -> Quote | None:
    if msg.get("event_type") != "book":
        return None
    return parse_book(ClobBook.model_validate(msg))


@dataclass(frozen=True)
class ClobAuth:
    api_key: str
    secret: str
    passphrase: str

    @classmethod
    def from_env(cls) -> ClobAuth | None:
        key = os.environ.get("CLOB_API_KEY")
        secret = os.environ.get("CLOB_API_SECRET")
        passphrase = os.environ.get("CLOB_API_PASSPHRASE")
        if key and secret and passphrase:
            return cls(api_key=key, secret=secret, passphrase=passphrase)
        return None


def _auth_headers(auth: ClobAuth) -> dict[str, str]:
    # Identity headers only; HMAC request signing is Phase 5. Secret is not sent.
    return {"POLY_API_KEY": auth.api_key, "POLY_PASSPHRASE": auth.passphrase}


class ClobClient:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = CLOB_BASE_URL,
        auth: ClobAuth | None = None,
        limiter: RateLimiter | None = None,
        max_retries: int = 3,
        retry_backoff: float = 0.5,
    ) -> None:
        headers = _auth_headers(auth) if auth is not None else {}
        self._client = httpx.AsyncClient(
            base_url=base_url, transport=transport, headers=headers
        )
        self._limiter = limiter or RateLimiter()
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_book(self, token_id: str) -> Quote:
        raw = await get_json(
            self._client,
            "/book",
            {"token_id": token_id},
            limiter=self._limiter,
            max_retries=self._max_retries,
            retry_backoff=self._retry_backoff,
        )
        return parse_book(ClobBook.model_validate(raw))

    async def fetch_price_history(self, token_id: str) -> list[Quote]:
        raw = await get_json(
            self._client,
            "/prices-history",
            {"market": token_id},
            limiter=self._limiter,
            max_retries=self._max_retries,
            retry_backoff=self._retry_backoff,
        )
        return parse_price_history(token_id, ClobPriceHistory.model_validate(raw))


class ClobWsClient:
    def __init__(
        self,
        *,
        ws_connect: Callable[..., Any] | None = None,
        url: str = CLOB_WS_URL,
    ) -> None:
        self._connect: Callable[..., Any] = ws_connect or websockets.connect
        self._url = url

    async def stream(self, market_ids: Sequence[str]) -> AsyncIterator[Quote]:
        async with self._connect(self._url) as ws:
            await ws.send(json.dumps({"type": "subscribe", "markets": list(market_ids)}))
            async for raw in ws:
                quote = parse_ws_message(json.loads(raw))
                if quote is not None:
                    yield quote
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `nix develop --command uv run pytest tests/data/test_clob.py -v`
Expected: all `PASSED`.

- [ ] **Step 6: Run the gates**

Run: `nix develop --command bash -c 'uv run ruff check && uv run ruff format --check && uv run mypy'`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add data/clob.py tests/fixtures/clob tests/data/test_clob.py
git commit -m "feat(data): CLOB adapter — parsers, REST client, WS client"
```

---

### Task 7: Reference-price interface + replay (`data/reference.py`)

**Files:**
- Create: `data/reference.py`
- Create: `tests/data/test_reference.py`

**Interfaces:**
- Consumes: `Quote` (Task 3).
- Produces:
  - `class ReferencePrice(Protocol)`: `def at(self, market_id: str, ts: datetime) -> Decimal | None`.
  - `class ReplayReference`: `__init__(self, quotes: Sequence[Quote])`; `at` returns the latest quote price with `quote.ts <= ts` for that market, else `None`. Never returns a quote later than `ts`.

- [ ] **Step 1: Write the failing tests**

`tests/data/test_reference.py`:
```python
"""Reference-price replay: as-of semantics, never returns a future quote."""

from datetime import UTC, datetime
from decimal import Decimal

from data.events import Quote
from data.reference import ReplayReference


def _q(minute: int, price: str) -> Quote:
    return Quote(
        market_id="m1",
        ts=datetime(2024, 6, 1, 0, minute, tzinfo=UTC),
        price=Decimal(price),
    )


def test_returns_latest_at_or_before_ts() -> None:
    ref = ReplayReference([_q(1, "0.40"), _q(3, "0.44"), _q(5, "0.48")])
    assert ref.at("m1", datetime(2024, 6, 1, 0, 4, tzinfo=UTC)) == Decimal("0.44")


def test_returns_exact_match() -> None:
    ref = ReplayReference([_q(1, "0.40"), _q(3, "0.44")])
    assert ref.at("m1", datetime(2024, 6, 1, 0, 3, tzinfo=UTC)) == Decimal("0.44")


def test_none_before_first_quote() -> None:
    ref = ReplayReference([_q(3, "0.44")])
    assert ref.at("m1", datetime(2024, 6, 1, 0, 1, tzinfo=UTC)) is None


def test_none_for_unknown_market() -> None:
    ref = ReplayReference([_q(1, "0.40")])
    assert ref.at("other", datetime(2024, 6, 1, 0, 5, tzinfo=UTC)) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix develop --command uv run pytest tests/data/test_reference.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'data.reference'`.

- [ ] **Step 3: Implement `data/reference.py`**

```python
"""Reference-price interface + a fixture-backed replay implementation.

The replay honors the same as-of discipline as the harness: it never returns a
quote with a timestamp after the requested ts. A live Betfair adapter is deferred
to the phase whose signal first consumes it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from data.events import Quote


class ReferencePrice(Protocol):
    def at(self, market_id: str, ts: datetime) -> Decimal | None: ...


class ReplayReference:
    def __init__(self, quotes: Sequence[Quote]) -> None:
        self._by_market: dict[str, list[Quote]] = {}
        for quote in sorted(quotes, key=lambda q: q.ts):
            self._by_market.setdefault(quote.market_id, []).append(quote)

    def at(self, market_id: str, ts: datetime) -> Decimal | None:
        result: Decimal | None = None
        for quote in self._by_market.get(market_id, []):
            if quote.ts <= ts:
                result = quote.price
            else:
                break
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix develop --command uv run pytest tests/data/test_reference.py -v`
Expected: all `PASSED`.

- [ ] **Step 5: Run the gates**

Run: `nix develop --command bash -c 'uv run ruff check && uv run ruff format --check && uv run mypy'`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add data/reference.py tests/data/test_reference.py
git commit -m "feat(data): reference-price Protocol + replay implementation"
```

---

### Task 8: Event feed + look-ahead error (`backtest/feed.py`)

**Files:**
- Create: `backtest/feed.py`
- Create: `tests/backtest/__init__.py` (empty)
- Create: `tests/backtest/test_feed.py`

**Interfaces:**
- Consumes: `MarketEvent` (Task 3).
- Produces:
  - `class LookAheadError(Exception)`.
  - `load_events(events: Iterable[MarketEvent]) -> list[MarketEvent]` — returns the events as a list, raising `LookAheadError` if any event's `ts` is earlier than its predecessor (rejects rather than silently sorts).

- [ ] **Step 1: Write the failing tests**

`tests/backtest/__init__.py`: empty file.

`tests/backtest/test_feed.py`:
```python
"""Event feed: chronology validation; out-of-order injection is rejected."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from backtest.feed import LookAheadError, load_events
from data.events import MarketEvent, Quote, event_from_quote


def _event(minute: int) -> MarketEvent:
    quote = Quote(
        market_id="m1",
        ts=datetime(2024, 6, 1, 0, minute, tzinfo=UTC),
        price=Decimal("0.50"),
    )
    return event_from_quote(quote)


def test_load_events_accepts_chronological() -> None:
    events = load_events([_event(1), _event(2), _event(2), _event(3)])
    assert [e.ts.minute for e in events] == [1, 2, 2, 3]


def test_load_events_rejects_future_injection() -> None:
    # A later-timestamped event followed by an earlier one = out-of-order leak.
    with pytest.raises(LookAheadError):
        load_events([_event(1), _event(5), _event(2)])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix develop --command uv run pytest tests/backtest/test_feed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backtest.feed'`.

- [ ] **Step 3: Implement `backtest/feed.py`**

```python
"""Event feed: validate chronological ordering. First line of the look-ahead guard.

We reject out-of-order events rather than silently sorting, so a mis-ordered feed
cannot mask a look-ahead bug. Merging multiple markets into one chronological
stream is the caller's responsibility.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from data.events import MarketEvent


class LookAheadError(Exception):
    """Raised when data ordering would allow seeing the future."""


def load_events(events: Iterable[MarketEvent]) -> list[MarketEvent]:
    ordered: list[MarketEvent] = []
    previous: datetime | None = None
    for event in events:
        if previous is not None and event.ts < previous:
            raise LookAheadError(
                f"event ts {event.ts} precedes previous {previous}; "
                "events must be chronological"
            )
        ordered.append(event)
        previous = event.ts
    return ordered
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix develop --command uv run pytest tests/backtest/test_feed.py -v`
Expected: both `PASSED`.

- [ ] **Step 5: Run the gates**

Run: `nix develop --command bash -c 'uv run ruff check && uv run ruff format --check && uv run mypy'`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add backtest/feed.py tests/backtest/__init__.py tests/backtest/test_feed.py
git commit -m "feat(backtest): chronology-validating event feed + LookAheadError"
```

---

### Task 9: As-of MarketView (`backtest/view.py`)

**Files:**
- Create: `backtest/view.py`
- Create: `tests/backtest/test_view.py`

**Interfaces:**
- Consumes: `Quote` (Task 3), `LookAheadError` (Task 8), `ReferencePrice` (Task 7).
- Produces:
  - `class MarketView`: `__init__(self, as_of: datetime, quotes_by_market: Mapping[str, list[Quote]], reference: ReferencePrice | None = None)`; `as_of` property; `latest_price(market_id) -> Decimal | None`; `price_at(market_id, ts) -> Decimal | None` (raises `LookAheadError` if `ts > as_of`); `reference_at(market_id, ts) -> Decimal | None` (raises if `ts > as_of`).

- [ ] **Step 1: Write the failing tests**

`tests/backtest/test_view.py`:
```python
"""MarketView: as-of queries; any future query raises (structural guard)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backtest.feed import LookAheadError
from backtest.view import MarketView
from data.events import Quote
from data.reference import ReplayReference


def _q(minute: int, price: str) -> Quote:
    return Quote(
        market_id="m1",
        ts=datetime(2024, 6, 1, 0, minute, tzinfo=UTC),
        price=Decimal(price),
    )


def _view(as_of_minute: int) -> MarketView:
    as_of = datetime(2024, 6, 1, 0, as_of_minute, tzinfo=UTC)
    quotes = {"m1": [_q(1, "0.40"), _q(3, "0.44")]}
    ref = ReplayReference([_q(1, "0.60"), _q(3, "0.62")])
    return MarketView(as_of=as_of, quotes_by_market=quotes, reference=ref)


def test_latest_price() -> None:
    assert _view(3).latest_price("m1") == Decimal("0.44")


def test_price_at_returns_as_of_or_before() -> None:
    view = _view(3)
    ts = datetime(2024, 6, 1, 0, 2, tzinfo=UTC)
    assert view.price_at("m1", ts) == Decimal("0.40")


def test_price_at_future_raises() -> None:
    view = _view(3)
    future = view.as_of + timedelta(minutes=1)
    with pytest.raises(LookAheadError):
        view.price_at("m1", future)


def test_reference_at_future_raises() -> None:
    view = _view(3)
    future = view.as_of + timedelta(minutes=1)
    with pytest.raises(LookAheadError):
        view.reference_at("m1", future)


def test_reference_at_returns_value() -> None:
    view = _view(3)
    assert view.reference_at("m1", view.as_of) == Decimal("0.62")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix develop --command uv run pytest tests/backtest/test_view.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backtest.view'`.

- [ ] **Step 3: Implement `backtest/view.py`**

```python
"""MarketView: an immutable, time-bounded view of market history.

The strategy only ever receives a MarketView, never the raw feed. Any query for a
timestamp after as_of raises LookAheadError, so the future is unreachable by
construction. The engine guarantees quotes_by_market holds only quotes with
ts <= as_of when it builds the view.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from backtest.feed import LookAheadError
from data.events import Quote
from data.reference import ReferencePrice


class MarketView:
    def __init__(
        self,
        as_of: datetime,
        quotes_by_market: Mapping[str, list[Quote]],
        reference: ReferencePrice | None = None,
    ) -> None:
        self._as_of = as_of
        self._quotes = quotes_by_market
        self._reference = reference

    @property
    def as_of(self) -> datetime:
        return self._as_of

    def _guard(self, ts: datetime) -> None:
        if ts > self._as_of:
            raise LookAheadError(f"query ts {ts} is after as_of {self._as_of}")

    def latest_price(self, market_id: str) -> Decimal | None:
        quotes = self._quotes.get(market_id, [])
        return quotes[-1].price if quotes else None

    def price_at(self, market_id: str, ts: datetime) -> Decimal | None:
        self._guard(ts)
        result: Decimal | None = None
        for quote in self._quotes.get(market_id, []):
            if quote.ts <= ts:
                result = quote.price
            else:
                break
        return result

    def reference_at(self, market_id: str, ts: datetime) -> Decimal | None:
        self._guard(ts)
        if self._reference is None:
            return None
        return self._reference.at(market_id, ts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix develop --command uv run pytest tests/backtest/test_view.py -v`
Expected: all `PASSED`.

- [ ] **Step 5: Run the gates**

Run: `nix develop --command bash -c 'uv run ruff check && uv run ruff format --check && uv run mypy'`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add backtest/view.py tests/backtest/test_view.py
git commit -m "feat(backtest): as-of MarketView with structural look-ahead guard"
```

---

### Task 10: Replay engine + Strategy protocol (`backtest/engine.py`, `backtest/strategy.py`)

**Files:**
- Create: `backtest/strategy.py`
- Create: `backtest/engine.py`
- Create: `tests/backtest/test_engine.py`

**Interfaces:**
- Consumes: `MarketEvent`, `Quote` (Task 3); `load_events`, `LookAheadError` (Task 8); `MarketView` (Task 9); `ReferencePrice` (Task 7); `Decision`, `Fill`, `RiskLimits`, `Side` (Phase 1 `core.models`); `realized_pnl`, `roi` (Phase 1 `core.metrics`).
- Produces:
  - `class Strategy(Protocol)`: `def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None`.
  - `class BacktestResult` (frozen dataclass): `fills: tuple[Fill, ...]`, `realized_pnl: Decimal`, `roi: float`.
  - `replay(events: Iterable[MarketEvent], strategy: Strategy, limits: RiskLimits, *, reference: ReferencePrice | None = None) -> BacktestResult`. Minimal deterministic fill model: an `ACT` decision with positive shares opens a position in that market at the traded token's current price; the next event for that market closes it at the then-current token price; any still-open position is closed at its last quote at the end. `costs_usd` is `Decimal(0)` (realistic costs/slippage are Phase 4).

- [ ] **Step 1: Write the failing tests**

`tests/backtest/test_engine.py`:
```python
"""Replay engine: deterministic fills, P&L via core.metrics, view forbids future."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backtest.engine import BacktestResult, replay
from backtest.feed import LookAheadError
from backtest.view import MarketView
from core.models import Decision, GateResult, RiskLimits, Side, SizingResult
from data.events import MarketEvent, Quote, event_from_quote


def _event(minute: int, price: str) -> MarketEvent:
    return event_from_quote(
        Quote(
            market_id="m1",
            ts=datetime(2024, 6, 1, 0, minute, tzinfo=UTC),
            price=Decimal(price),
        )
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        bankroll=Decimal("25"),
        kelly_fraction=0.25,
        max_position_fraction=0.2,
        max_position_usd=Decimal("5"),
    )


class _BuyBelowHalf:
    """Stateless synthetic strategy: buy YES whenever price < 0.50."""

    def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
        if event.quote.price < Decimal("0.50"):
            return Decision(
                gate=GateResult.act(side=Side.BUY_YES, edge=0.1),
                sizing=SizingResult(stake_usd=Decimal("5"), shares=Decimal("10")),
            )
        return None


def test_replay_open_then_close_pnl() -> None:
    # Buy 10 YES at 0.40 (event 1), close at 0.55 (event 2). pnl = 10*(0.55-0.40).
    result = replay([_event(1, "0.40"), _event(2, "0.55")], _BuyBelowHalf(), _limits())
    assert isinstance(result, BacktestResult)
    assert result.realized_pnl == Decimal("1.50")
    assert len(result.fills) == 1


def test_replay_is_deterministic() -> None:
    events = [_event(1, "0.40"), _event(2, "0.55"), _event(3, "0.48")]
    r1 = replay(events, _BuyBelowHalf(), _limits())
    r2 = replay(events, _BuyBelowHalf(), _limits())
    assert r1 == r2


def test_replay_no_trades_when_strategy_abstains() -> None:
    class _NeverActs:
        def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
            return None

    result = replay([_event(1, "0.40"), _event(2, "0.55")], _NeverActs(), _limits())
    assert result.fills == ()
    assert result.realized_pnl == Decimal("0")


def test_strategy_view_forbids_future() -> None:
    captured: dict[str, MarketView] = {}

    class _Spy:
        def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
            captured["view"] = view
            return None

    replay([_event(1, "0.40")], _Spy(), _limits())
    view = captured["view"]
    future = view.as_of + timedelta(minutes=1)
    with pytest.raises(LookAheadError):
        view.price_at("m1", future)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix develop --command uv run pytest tests/backtest/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backtest.engine'`.

- [ ] **Step 3: Implement `backtest/strategy.py`**

```python
"""The Strategy seam: how signals plug into the replay harness.

Phase 2 ships only synthetic strategies (in tests). Phase 3 signals and the
Phase 4 assembled pipeline implement this same Protocol.
"""

from __future__ import annotations

from typing import Protocol

from backtest.view import MarketView
from core.models import Decision
from data.events import MarketEvent


class Strategy(Protocol):
    def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None: ...
```

- [ ] **Step 4: Implement `backtest/engine.py`**

```python
"""Deterministic replay engine.

Pushes chronologically-ordered events through a Strategy, building an as-of
MarketView per event. Uses a minimal deterministic fill model so P&L is
reproducible; realistic fills/slippage are Phase 4. No wall-clock, no RNG.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from backtest.feed import load_events
from backtest.strategy import Strategy
from backtest.view import MarketView
from core.metrics import realized_pnl, roi
from core.models import Fill, RiskLimits, Side
from data.events import Quote
from data.reference import ReferencePrice


@dataclass(frozen=True)
class BacktestResult:
    fills: tuple[Fill, ...]
    realized_pnl: Decimal
    roi: float


@dataclass
class _OpenPosition:
    side: Side
    entry_price: Decimal
    shares: Decimal


def _token_price(side: Side, yes_price: Decimal) -> Decimal:
    return yes_price if side is Side.BUY_YES else Decimal(1) - yes_price


def replay(
    events: Iterable[MarketEvent],
    strategy: Strategy,
    limits: RiskLimits,
    *,
    reference: ReferencePrice | None = None,
) -> BacktestResult:
    ordered = load_events(events)
    quotes_by_market: dict[str, list[Quote]] = {}
    open_positions: dict[str, _OpenPosition] = {}
    fills: list[Fill] = []
    deployed = Decimal(0)

    for event in ordered:
        quotes_by_market.setdefault(event.market_id, []).append(event.quote)
        view = MarketView(event.ts, quotes_by_market, reference)
        decision = strategy.on_event(event, view)

        market = event.market_id
        yes_price = event.quote.price

        if market in open_positions:
            position = open_positions.pop(market)
            exit_price = _token_price(position.side, yes_price)
            fills.append(
                Fill(
                    side=position.side,
                    entry_price=position.entry_price,
                    exit_price=exit_price,
                    shares=position.shares,
                    costs_usd=Decimal(0),
                )
            )
            deployed += position.entry_price * position.shares

        if (
            decision is not None
            and decision.gate.action == "act"
            and decision.gate.side is not None
            and decision.sizing.shares > 0
        ):
            side = decision.gate.side
            open_positions[market] = _OpenPosition(
                side=side,
                entry_price=_token_price(side, yes_price),
                shares=decision.sizing.shares,
            )

    for market, position in open_positions.items():
        last_yes_price = quotes_by_market[market][-1].price
        exit_price = _token_price(position.side, last_yes_price)
        fills.append(
            Fill(
                side=position.side,
                entry_price=position.entry_price,
                exit_price=exit_price,
                shares=position.shares,
                costs_usd=Decimal(0),
            )
        )
        deployed += position.entry_price * position.shares

    pnl = realized_pnl(fills)
    return BacktestResult(
        fills=tuple(fills),
        realized_pnl=pnl,
        roi=roi(pnl, deployed) if deployed > 0 else 0.0,
    )
```

Note on the worked test: at event 1 (`0.40 < 0.50`) the strategy opens BUY_YES @ 0.40 ×10. At event 2 (`0.55`) the open position closes first at 0.55 (`pnl = 10×(0.55−0.40) = 1.50`); the strategy abstains at 0.55 (not `< 0.50`), so no new position opens. One fill, `pnl == 1.50`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `nix develop --command uv run pytest tests/backtest/test_engine.py -v`
Expected: all `PASSED`.

- [ ] **Step 6: Run the gates**

Run: `nix develop --command bash -c 'uv run ruff check && uv run ruff format --check && uv run mypy'`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add backtest/strategy.py backtest/engine.py tests/backtest/test_engine.py
git commit -m "feat(backtest): deterministic replay engine + Strategy protocol"
```

---

### Task 11: Walk-forward splitter (`backtest/walkforward.py`)

**Files:**
- Create: `backtest/walkforward.py`
- Create: `tests/backtest/test_walkforward.py`

**Interfaces:**
- Produces:
  - `class Split` (frozen dataclass): `train: range`, `test: range`.
  - `walk_forward_splits(n: int, *, train_size: int, test_size: int, step: int | None = None, expanding: bool = False) -> list[Split]` — over index space `[0, n)`; each `Split` has `test.start == train.stop`; `step` defaults to `test_size`; `expanding=True` anchors `train.start == 0`. Raises `ValueError` for non-positive sizes.

- [ ] **Step 1: Write the failing tests**

`tests/backtest/test_walkforward.py`:
```python
"""Walk-forward splitter: ordered, non-overlapping, no leakage (property)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from backtest.walkforward import Split, walk_forward_splits


def test_rolling_splits_basic() -> None:
    splits = walk_forward_splits(10, train_size=4, test_size=2)
    assert splits[0] == Split(train=range(0, 4), test=range(4, 6))
    assert splits[1] == Split(train=range(2, 6), test=range(6, 8))
    assert splits[2] == Split(train=range(4, 8), test=range(8, 10))
    assert len(splits) == 3


def test_expanding_anchors_train_at_zero() -> None:
    splits = walk_forward_splits(10, train_size=4, test_size=2, expanding=True)
    assert all(s.train.start == 0 for s in splits)
    assert splits[1].train == range(0, 6)


def test_rejects_nonpositive_sizes() -> None:
    with pytest.raises(ValueError):
        walk_forward_splits(10, train_size=0, test_size=2)


@given(
    n=st.integers(min_value=0, max_value=200),
    train_size=st.integers(min_value=1, max_value=50),
    test_size=st.integers(min_value=1, max_value=50),
)
def test_splits_are_ordered_and_leak_free(n: int, train_size: int, test_size: int) -> None:
    splits = walk_forward_splits(n, train_size=train_size, test_size=test_size)
    for s in splits:
        # test starts exactly where train ends: no overlap, no gap, no leakage.
        assert s.test.start == s.train.stop
        assert s.train.start >= 0
        assert s.test.stop <= n
        assert len(s.train) == train_size
        assert len(s.test) == test_size
    # default step == test_size => consecutive test windows are contiguous.
    for earlier, later in zip(splits, splits[1:], strict=False):
        assert later.test.start == earlier.test.stop
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix develop --command uv run pytest tests/backtest/test_walkforward.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backtest.walkforward'`.

- [ ] **Step 3: Implement `backtest/walkforward.py`**

```python
"""Index-based walk-forward splitter over the sorted-event index space [0, n).

Each Split's test window starts exactly where its train window ends, so there is
no overlap and no leakage. Index-based (not timedelta-based) for deterministic,
fence-post-free splitting; the caller maps ranges onto its sorted events.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Split:
    train: range
    test: range


def walk_forward_splits(
    n: int,
    *,
    train_size: int,
    test_size: int,
    step: int | None = None,
    expanding: bool = False,
) -> list[Split]:
    if train_size <= 0 or test_size <= 0:
        raise ValueError("train_size and test_size must be positive")
    advance = step if step is not None else test_size
    if advance <= 0:
        raise ValueError("step must be positive")

    splits: list[Split] = []
    start = 0
    while True:
        train_end = start + train_size
        test_end = train_end + test_size
        if test_end > n:
            break
        train_start = 0 if expanding else start
        splits.append(Split(train=range(train_start, train_end), test=range(train_end, test_end)))
        start += advance
    return splits
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix develop --command uv run pytest tests/backtest/test_walkforward.py -v`
Expected: all `PASSED` (the property test runs many examples).

- [ ] **Step 5: Run the gates**

Run: `nix develop --command bash -c 'uv run ruff check && uv run ruff format --check && uv run mypy'`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add backtest/walkforward.py tests/backtest/test_walkforward.py
git commit -m "feat(backtest): index-based walk-forward splitter"
```

---

### Task 12: Walk-forward report (`backtest/report.py`)

**Files:**
- Create: `backtest/report.py`
- Create: `tests/backtest/test_report.py`

**Interfaces:**
- Consumes: `BacktestResult` (Task 10).
- Produces:
  - `class WalkForwardReport` (frozen dataclass): `per_split_pnl: tuple[Decimal, ...]`, `total_pnl: Decimal`, `mean_roi: float`.
  - `aggregate(results: Sequence[BacktestResult]) -> WalkForwardReport`.

- [ ] **Step 1: Write the failing tests**

`tests/backtest/test_report.py`:
```python
"""Walk-forward report: per-split aggregation of P&L and ROI."""

from decimal import Decimal

from backtest.engine import BacktestResult
from backtest.report import WalkForwardReport, aggregate


def _result(pnl: str, roi_: float) -> BacktestResult:
    return BacktestResult(fills=(), realized_pnl=Decimal(pnl), roi=roi_)


def test_aggregate_sums_pnl_and_means_roi() -> None:
    report = aggregate([_result("1.50", 0.06), _result("-0.50", -0.02)])
    assert isinstance(report, WalkForwardReport)
    assert report.per_split_pnl == (Decimal("1.50"), Decimal("-0.50"))
    assert report.total_pnl == Decimal("1.00")
    assert report.mean_roi == 0.02


def test_aggregate_empty() -> None:
    report = aggregate([])
    assert report.total_pnl == Decimal("0")
    assert report.mean_roi == 0.0
    assert report.per_split_pnl == ()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix develop --command uv run pytest tests/backtest/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backtest.report'`.

- [ ] **Step 3: Implement `backtest/report.py`**

```python
"""Thin per-split aggregation of backtest results into a walk-forward report.

Reporting only — no new financial logic. Brier aggregation arrives once signals
emit probabilities (Phase 3).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from backtest.engine import BacktestResult


@dataclass(frozen=True)
class WalkForwardReport:
    per_split_pnl: tuple[Decimal, ...]
    total_pnl: Decimal
    mean_roi: float


def aggregate(results: Sequence[BacktestResult]) -> WalkForwardReport:
    per_split_pnl = tuple(r.realized_pnl for r in results)
    total_pnl = sum(per_split_pnl, Decimal(0))
    mean_roi = sum(r.roi for r in results) / len(results) if results else 0.0
    return WalkForwardReport(
        per_split_pnl=per_split_pnl,
        total_pnl=total_pnl,
        mean_roi=mean_roi,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix develop --command uv run pytest tests/backtest/test_report.py -v`
Expected: both `PASSED`.

- [ ] **Step 5: Run the gates**

Run: `nix develop --command bash -c 'uv run ruff check && uv run ruff format --check && uv run mypy'`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add backtest/report.py tests/backtest/test_report.py
git commit -m "feat(backtest): walk-forward P&L/ROI report aggregation"
```

---

### Task 13 (optional): Real-payload recording helper (`scripts/record_fixtures.py`)

Optional, human-run, network-using helper to replace the hand-authored fixtures with sanitized real captures. **Not part of the test suite or the acceptance gate.** Skip unless the user wants real captures now.

**Files:**
- Create: `scripts/record_fixtures.py`

- [ ] **Step 1: Implement the recorder**

```python
"""Capture real Gamma/CLOB payloads into tests/fixtures/ (human-run, network on).

Run outside the test suite. Sanitize before committing: never write auth headers
or secrets into a fixture. Usage:
    nix develop --command uv run python scripts/record_fixtures.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from data.clob import CLOB_BASE_URL
from data.gamma import GAMMA_BASE_URL

FIX = Path(__file__).parent.parent / "tests" / "fixtures"


async def main() -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        markets = (await client.get(f"{GAMMA_BASE_URL}/markets", params={"limit": 3})).json()
        (FIX / "gamma" / "markets.json").write_text(json.dumps(markets, indent=2))

        if markets:
            token_id = markets[0]["clobTokenIds"][0]
            book = (await client.get(f"{CLOB_BASE_URL}/book", params={"token_id": token_id})).json()
            (FIX / "clob" / "book.json").write_text(json.dumps(book, indent=2))

    print("Recorded fixtures. Review and sanitize before committing.")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Commit (only if the user wants this helper)**

```bash
git add scripts/record_fixtures.py
git commit -m "chore(data): optional real-payload fixture recorder"
```

---

## Final acceptance check (Phase 2 gate)

Run all gates fresh inside the devshell:

```bash
nix develop --command bash -c '
  uv sync --frozen &&
  uv run ruff check &&
  uv run ruff format --check &&
  uv run mypy &&
  uv run pytest --cov
'
```
Expected: every command exits 0 — ruff clean, format clean, mypy `Success`, all unit + property tests `passed`, with **no live network** reachable from any test (the `tests/conftest.py` guard blocks DNS).

Confirm the PLAN.md Phase 2 invariants are each proven by a test:
- adapters parse recorded payloads, no live network → `tests/data/test_payloads.py`, `test_gamma.py`, `test_clob.py` (all over fixtures / `MockTransport` / fake WS; `conftest._no_network` active)
- harness rejects a deliberately injected future-peek → `test_feed.py::test_load_events_rejects_future_injection`, `test_view.py::test_price_at_future_raises`, `test_engine.py::test_strategy_view_forbids_future`
- same input yields identical P&L on repeat runs → `test_engine.py::test_replay_is_deterministic`
- out-of-sample splits, no leakage → `test_walkforward.py::test_splits_are_ordered_and_leak_free`

Then mark Phase 2 in `PLAN.md`:
- [ ] Change `- [ ] Phase 2 — Data & backtest` to `- [x] Phase 2 — Data & backtest`, commit with `docs: mark Phase 2 complete`.

This satisfies `PLAN.md`'s Phase 2 gate: "adapters parse recorded payloads with no live network in tests; the harness rejects a deliberately injected future-peek; same input yields identical P&L on repeat runs."
```
