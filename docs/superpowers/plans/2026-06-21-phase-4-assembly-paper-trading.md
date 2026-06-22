# Phase 4 — Assembly & Paper Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Assemble the tested parts into one paper-trading pipeline (signals → calibration → gate → sizing → simulated maker fills) running end-to-end over historical and live feeds with **zero real orders**, plus a mocked `pydantic-ai` shadow S3 agent.

**Architecture:** A pure maker-first fill model (`core/fills.py`) is shared by the synchronous backtest engine and a new thin async orchestrator (`app/`). A `Feed` seam unifies historical replay and live streaming. Signals compose by **priority precedence** (S2 > S1 > S3); S3 is an **unpromoted shadow** agent that logs calibrated decisions but never opens a position. Walk-forward fits one calibrator per split on the train window only. All financial logic stays in `core/`; `app/` and `data/` are thin edges.

**Tech Stack:** Python 3.12+, `uv`, `pydantic` v2, `numpy`/`scikit-learn`, `httpx`/`websockets`, `polars`, new dependency `pydantic-ai`; tests with `pytest` + `hypothesis`, async tests via `asyncio.run` (no `pytest-asyncio`).

## Global Constraints

- TDD: write the failing test first; a step is done only when its tests are green plus `ruff` and `mypy` (strict) are clean. Do not advance with a red gate.
- No real network in any test. The autouse `tests/conftest.py` guard blocks `socket.getaddrinfo`; mock the LLM model and all clients (injected fakes / `httpx.MockTransport`).
- Pure core, thin edges: `core/` must not import `data`/`backtest`/`app`. Financial logic is pure and unit/property-tested in isolation.
- Cost gate is law: never emit a trade when `abs(edge) < hurdle`. Reuse `core.decision.evaluate`; never re-implement the hurdle in a signal.
- No look-ahead: a decision at time `t` reads only data with ts ≤ `t` (enforced by `MarketView`). The fill simulator's forward window `(t, t+expiry]` is **not** look-ahead. A calibrator is fit only on train-window samples.
- Maker-first: a resting order fills only if a later in-window quote trades through it; otherwise it expires unfilled.
- Decimal for prices/quotes/cash; float for statistical/decision math (`p_fair`, edges, Brier).
- Secrets from env only (`ODDS_API_KEY`, `CLOB_API_*`); never in code, fixtures, logs, or commits.
- Commands run inside `nix develop`. Run lint/types/tests with: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest`.
- One dependency addition this phase: `pydantic-ai` (single `uv lock` at Task 9).
- Reference spec: `docs/superpowers/specs/2026-06-21-phase-4-assembly-paper-trading-design.md`.

---

### Task 1: Maker-first fill model (`core/fills.py`)

**Files:**
- Create: `core/fills.py`
- Test: `tests/core/test_fills.py`

**Interfaces:**
- Consumes: `core.models.Side`, `core.models.CostInputs`, `core.models.Fill`, `data.events.Quote`.
- Produces:
  - `MakerOrder(side: Side, limit_price: Decimal, shares: Decimal, placed_ts: datetime, expiry_ts: datetime)` (frozen dataclass).
  - `crosses(order: MakerOrder, quote: Quote) -> bool` — true iff `placed_ts < quote.ts <= expiry_ts` and the quote's token price trades through the order's limit.
  - `simulate_maker_fill(order: MakerOrder, future_quotes: Sequence[Quote]) -> datetime | None` — the ts of the first crossing quote (entry executes at `order.limit_price`'s token price), else `None`.
  - `round_trip_fill_costs(costs: CostInputs, entry_price: Decimal, exit_price: Decimal, shares: Decimal) -> Decimal` — `fee_rate*(entry+exit)*shares + gas_usd`.
  - `token_price(side: Side, yes_price: Decimal) -> Decimal` — YES price for BUY_YES, `1 - yes_price` for BUY_NO.

> **Note on look-ahead:** `crosses` is the single shared predicate. The engine (Task 3) and orchestrator (Task 4) call it incrementally as later quotes arrive; the pure `simulate_maker_fill` is the in-isolation source of truth. The forward window is legitimate forward simulation of a resting order, not a decision reading the future.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_fills.py
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from core.fills import (
    MakerOrder,
    crosses,
    round_trip_fill_costs,
    simulate_maker_fill,
    token_price,
)
from core.models import CostInputs, Side
from data.events import Quote

_T0 = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


def _quote(minute: int, price: str) -> Quote:
    return Quote(market_id="m", ts=_T0 + timedelta(minutes=minute), price=Decimal(price))


def _order(side: Side, limit: str, expiry_min: int = 10) -> MakerOrder:
    return MakerOrder(
        side=side,
        limit_price=Decimal(limit),
        shares=Decimal("10"),
        placed_ts=_T0,
        expiry_ts=_T0 + timedelta(minutes=expiry_min),
    )


def test_token_price_buy_no_complements() -> None:
    assert token_price(Side.BUY_YES, Decimal("0.40")) == Decimal("0.40")
    assert token_price(Side.BUY_NO, Decimal("0.40")) == Decimal("0.60")


def test_buy_yes_fills_when_price_drops_to_limit() -> None:
    order = _order(Side.BUY_YES, "0.40")
    # later quote at 0.40 trades through our resting bid
    assert crosses(order, _quote(2, "0.40")) is True
    assert simulate_maker_fill(order, [_quote(1, "0.45"), _quote(2, "0.40")]) == _quote(
        2, "0.40"
    ).ts


def test_buy_yes_does_not_fill_when_price_rises() -> None:
    order = _order(Side.BUY_YES, "0.40")
    assert crosses(order, _quote(2, "0.55")) is False
    assert simulate_maker_fill(order, [_quote(1, "0.50"), _quote(2, "0.55")]) is None


def test_buy_no_fills_when_yes_rises_to_limit() -> None:
    order = _order(Side.BUY_NO, "0.60")  # NO token limit = 0.40
    assert crosses(order, _quote(2, "0.60")) is True  # yes>=0.60 -> no token<=0.40
    assert crosses(order, _quote(2, "0.55")) is False


def test_quote_outside_window_never_crosses() -> None:
    order = _order(Side.BUY_YES, "0.40", expiry_min=3)
    assert crosses(order, _quote(0, "0.30")) is False  # at/before placed_ts
    assert crosses(order, _quote(5, "0.30")) is False  # after expiry


def test_round_trip_costs() -> None:
    costs = CostInputs(
        spread=Decimal("0"),
        fee_rate=Decimal("0.02"),
        gas_usd=Decimal("0.01"),
        model_error_margin=Decimal("0"),
    )
    # 0.02*(0.40+0.55)*10 + 0.01 = 0.19 + 0.01 = 0.20
    assert round_trip_fill_costs(
        costs, Decimal("0.40"), Decimal("0.55"), Decimal("10")
    ) == Decimal("0.20")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_fills.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.fills'`.

- [ ] **Step 3: Write minimal implementation**

```python
# core/fills.py
"""Maker-first fill model: a resting limit fills only if a later quote crosses it.

Pure. Shared by the backtest engine and the paper orchestrator. The forward
window is forward simulation of an order's life, not look-ahead.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from core.models import CostInputs, Side
from data.events import Quote


@dataclass(frozen=True)
class MakerOrder:
    side: Side
    limit_price: Decimal
    shares: Decimal
    placed_ts: datetime
    expiry_ts: datetime


def token_price(side: Side, yes_price: Decimal) -> Decimal:
    return yes_price if side is Side.BUY_YES else Decimal(1) - yes_price


def crosses(order: MakerOrder, quote: Quote) -> bool:
    if not (order.placed_ts < quote.ts <= order.expiry_ts):
        return False
    return token_price(order.side, quote.price) <= token_price(
        order.side, order.limit_price
    )


def simulate_maker_fill(
    order: MakerOrder, future_quotes: Sequence[Quote]
) -> datetime | None:
    for quote in future_quotes:
        if crosses(order, quote):
            return quote.ts
    return None


def round_trip_fill_costs(
    costs: CostInputs, entry_price: Decimal, exit_price: Decimal, shares: Decimal
) -> Decimal:
    return costs.fee_rate * (entry_price + exit_price) * shares + costs.gas_usd
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_fills.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Add a property test for the fill invariant**

```python
# append to tests/core/test_fills.py
from hypothesis import given
from hypothesis import strategies as st


@given(
    limit=st.decimals(min_value="0.02", max_value="0.98", places=2),
    drop=st.decimals(min_value="0.00", max_value="0.50", places=2),
)
def test_buy_yes_fills_iff_quote_reaches_limit(limit: Decimal, drop: Decimal) -> None:
    order = _order(Side.BUY_YES, str(limit))
    quote_price = max(Decimal("0.01"), min(Decimal("0.99"), limit - drop))
    quote = Quote(market_id="m", ts=_T0 + timedelta(minutes=1), price=quote_price)
    assert crosses(order, quote) == (quote_price <= limit)
```

- [ ] **Step 6: Run lint, types, tests**

Run: `uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest tests/core/test_fills.py -v`
Expected: all clean/PASS.

- [ ] **Step 7: Commit**

```bash
git add core/fills.py tests/core/test_fills.py
git commit -m "feat(core): maker-first fill model"
```

---

### Task 2: Feed abstraction (`app/feed.py`)

**Files:**
- Create: `app/feed.py`
- Test: `tests/app/__init__.py`, `tests/app/test_feed.py`

**Interfaces:**
- Consumes: `data.events.MarketEvent`, `data.events.event_from_quote`, `data.events.Quote`, `backtest.feed.load_events`, `data.clob.ClobWsClient`.
- Produces:
  - `Feed` Protocol: `def events(self) -> AsyncIterator[MarketEvent]`.
  - `HistoricalFeed(events: Sequence[MarketEvent])` — validates ordering via `load_events`, async-yields them.
  - `LiveFeed(ws: ClobWsClient, market_ids: Sequence[str])` — async-yields `event_from_quote(q)` for each quote off `ws.stream(market_ids)`.

- [ ] **Step 1: Create the test package marker**

```python
# tests/app/__init__.py
```

- [ ] **Step 2: Write the failing test**

```python
# tests/app/test_feed.py
import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.feed import HistoricalFeed, LiveFeed
from backtest.feed import LookAheadError
from data.events import MarketEvent, Quote, event_from_quote

_T0 = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


def _event(minute: int, price: str) -> MarketEvent:
    return event_from_quote(
        Quote(market_id="m", ts=_T0 + timedelta(minutes=minute), price=Decimal(price))
    )


def test_historical_feed_yields_in_order() -> None:
    async def _run() -> None:
        feed = HistoricalFeed([_event(0, "0.40"), _event(1, "0.55")])
        got = [e async for e in feed.events()]
        assert [e.quote.price for e in got] == [Decimal("0.40"), Decimal("0.55")]

    asyncio.run(_run())


def test_historical_feed_rejects_out_of_order() -> None:
    with pytest.raises(LookAheadError):
        HistoricalFeed([_event(2, "0.40"), _event(1, "0.55")])


class _FakeWsClient:
    def __init__(self, quotes: list[Quote]) -> None:
        self._quotes = quotes
        self.subscribed: list[str] = []

    async def stream(self, market_ids: list[str]) -> AsyncIterator[Quote]:
        self.subscribed = list(market_ids)
        for q in self._quotes:
            yield q


def test_live_feed_wraps_ws_quotes_into_events() -> None:
    async def _run() -> None:
        quotes = [
            Quote(market_id="m", ts=_T0, price=Decimal("0.40")),
            Quote(market_id="m", ts=_T0 + timedelta(minutes=1), price=Decimal("0.55")),
        ]
        ws = _FakeWsClient(quotes)
        feed = LiveFeed(ws, ["m"])
        got = [e async for e in feed.events()]
        assert ws.subscribed == ["m"]
        assert all(isinstance(e, MarketEvent) for e in got)
        assert [e.quote.price for e in got] == [Decimal("0.40"), Decimal("0.55")]

    asyncio.run(_run())
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/app/test_feed.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.feed'`.

- [ ] **Step 4: Write minimal implementation**

```python
# app/feed.py
"""The feed seam: one timestamped MarketEvent source for the orchestrator.

HistoricalFeed replays recorded events (deterministic, offline); LiveFeed wraps
the CLOB websocket stream. Both present the same async interface so the
orchestrator runs one code path over either origin.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from backtest.feed import load_events
from data.clob import ClobWsClient
from data.events import MarketEvent, event_from_quote


class Feed(Protocol):
    def events(self) -> AsyncIterator[MarketEvent]: ...


class HistoricalFeed:
    def __init__(self, events: Sequence[MarketEvent]) -> None:
        self._events = load_events(events)

    async def events(self) -> AsyncIterator[MarketEvent]:
        for event in self._events:
            yield event


class LiveFeed:
    def __init__(self, ws: ClobWsClient, market_ids: Sequence[str]) -> None:
        self._ws = ws
        self._market_ids = list(market_ids)

    async def events(self) -> AsyncIterator[MarketEvent]:
        async for quote in self._ws.stream(self._market_ids):
            yield event_from_quote(quote)
```

> `LiveFeed` is typed against `ClobWsClient`; the test's `_FakeWsClient` is structurally compatible (duck-typed `stream`). If mypy complains about the fake, keep the production type — the test double matches the runtime shape.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/app/test_feed.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run lint, types, full suite**

Run: `uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest`
Expected: all clean/PASS.

- [ ] **Step 7: Commit**

```bash
git add app/feed.py tests/app/__init__.py tests/app/test_feed.py
git commit -m "feat(app): feed abstraction unifying historical and live sources"
```

---

### Task 3: Engine adopts the maker fill model (`backtest/engine.py`)

**Files:**
- Modify: `backtest/engine.py`
- Modify: `tests/backtest/test_engine.py`

**Interfaces:**
- Consumes: `core.fills.MakerOrder`, `core.fills.crosses`, `core.fills.token_price`, `core.fills.round_trip_fill_costs`.
- Produces: `replay(events, strategy, limits, *, reference=None, fill_expiry: timedelta = timedelta(minutes=5)) -> BacktestResult` — unchanged signature plus `fill_expiry`. An ACT decision now rests a `MakerOrder`; the position opens only when a later in-window quote crosses the limit; it closes on the next quote for that market after the fill; round-trip costs apply.

> **Behavior change:** the Phase 2 engine filled frictionlessly and immediately. This task replaces that with the maker lifecycle, so two existing tests that assumed immediate fills are rewritten to maker semantics. `test_replay_records_signal_probs`, `test_replay_is_deterministic`, `test_replay_no_trades_when_strategy_abstains`, and `test_strategy_view_forbids_future` keep passing (signal-prob recording and the view guard are unaffected).

- [ ] **Step 1: Rewrite the open/close test to maker semantics**

Replace `test_replay_open_then_close_pnl` in `tests/backtest/test_engine.py` with:

```python
def test_replay_maker_fills_then_closes_pnl() -> None:
    # Order rests at 0.40 (event 1). Event 2 dips to 0.38 -> crosses -> fill at 0.40.
    # Once filled, the market is "open", so no new order rests; event 3 at 0.55
    # closes the position. pnl = 10*(0.55-0.40) - costs(0) = 1.50.
    result = replay(
        [_event(1, "0.40"), _event(2, "0.38"), _event(3, "0.55")],
        _BuyBelowHalf(),
        _limits(),
    )
    assert isinstance(result, BacktestResult)
    assert len(result.fills) == 1
    fill = result.fills[0]
    assert fill.entry_price == Decimal("0.40")
    assert fill.exit_price == Decimal("0.55")
    assert result.realized_pnl == Decimal("1.50")


def test_replay_order_expires_unfilled_when_price_never_crosses() -> None:
    # Rests at 0.40; price only rises -> never crosses -> no fill, no position.
    result = replay(
        [_event(1, "0.40"), _event(2, "0.55"), _event(3, "0.60")],
        _BuyBelowHalf(),
        _limits(),
    )
    assert result.fills == ()
    assert result.realized_pnl == Decimal("0")
```

> `_BuyBelowHalf` returns a fixed `SizingResult(shares=10)`; the Decision has no `prob`, so `signal_probs` stays empty — unaffected. Keep the other existing tests as-is.

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `uv run pytest tests/backtest/test_engine.py -v`
Expected: the two new tests FAIL (current engine fills immediately at 0.40 on event 1 and closes event 2), others PASS.

- [ ] **Step 3: Rewrite the engine loop to the maker lifecycle**

Replace the body of `backtest/engine.py` from the imports through `replay` with:

```python
"""Deterministic replay engine with a maker-first fill model.

An ACT decision rests a limit order; the position opens only when a later
in-window quote trades through the limit, and closes on the next quote for that
market. No wall-clock, no RNG. The fill simulator reads forward quotes directly
(not via MarketView) -- forward simulation of a resting order, not look-ahead.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from backtest.feed import load_events
from backtest.strategy import Strategy
from backtest.view import MarketView
from core.fills import MakerOrder, crosses, round_trip_fill_costs, token_price
from core.metrics import realized_pnl, roi
from core.models import CostInputs, Fill, RiskLimits, Side
from data.events import MarketEvent, Quote
from data.reference import ReferencePrice

_ZERO_COSTS = CostInputs(
    spread=Decimal(0),
    fee_rate=Decimal(0),
    gas_usd=Decimal(0),
    model_error_margin=Decimal(0),
)


@dataclass(frozen=True)
class BacktestResult:
    fills: tuple[Fill, ...]
    realized_pnl: Decimal
    roi: float
    signal_probs: tuple[tuple[str, float], ...] = ()


@dataclass
class _OpenPosition:
    side: Side
    entry_price: Decimal
    shares: Decimal
    opened_ts: datetime


def replay(
    events: Iterable[MarketEvent],
    strategy: Strategy,
    limits: RiskLimits,
    *,
    reference: ReferencePrice | None = None,
    fill_expiry: timedelta = timedelta(minutes=5),
    costs: CostInputs = _ZERO_COSTS,
) -> BacktestResult:
    ordered = load_events(events)
    quotes_by_market: dict[str, list[Quote]] = {}
    pending: dict[str, MakerOrder] = {}
    open_positions: dict[str, _OpenPosition] = {}
    fills: list[Fill] = []
    deployed = Decimal(0)
    signal_probs: list[tuple[str, float]] = []

    for event in ordered:
        quotes_by_market.setdefault(event.market_id, []).append(event.quote)
        view = MarketView(event.ts, quotes_by_market, reference)
        decision = strategy.on_event(event, view)
        if decision is not None and decision.prob is not None:
            signal_probs.append((event.market_id, decision.prob))

        market = event.market_id
        quote = event.quote

        # 1. resolve a resting order for this market
        if market in pending:
            order = pending[market]
            if crosses(order, quote):
                open_positions[market] = _OpenPosition(
                    side=order.side,
                    entry_price=token_price(order.side, order.limit_price),
                    shares=order.shares,
                    opened_ts=quote.ts,
                )
                del pending[market]
            elif quote.ts > order.expiry_ts:
                del pending[market]
        # 2. otherwise close an open position on a later quote
        elif market in open_positions and quote.ts > open_positions[market].opened_ts:
            position = open_positions.pop(market)
            exit_price = token_price(position.side, quote.price)
            fills.append(
                Fill(
                    side=position.side,
                    entry_price=position.entry_price,
                    exit_price=exit_price,
                    shares=position.shares,
                    costs_usd=round_trip_fill_costs(
                        costs, position.entry_price, exit_price, position.shares
                    ),
                )
            )
            deployed += position.entry_price * position.shares

        # 3. rest a new order when flat
        if (
            market not in pending
            and market not in open_positions
            and decision is not None
            and decision.gate.action == "act"
            and decision.gate.side is not None
            and decision.sizing.shares > 0
        ):
            pending[market] = MakerOrder(
                side=decision.gate.side,
                limit_price=quote.price,
                shares=decision.sizing.shares,
                placed_ts=quote.ts,
                expiry_ts=quote.ts + fill_expiry,
            )

    # mark out any still-open positions at their last seen price
    for market, position in open_positions.items():
        last_price = quotes_by_market[market][-1].price
        exit_price = token_price(position.side, last_price)
        fills.append(
            Fill(
                side=position.side,
                entry_price=position.entry_price,
                exit_price=exit_price,
                shares=position.shares,
                costs_usd=round_trip_fill_costs(
                    costs, position.entry_price, exit_price, position.shares
                ),
            )
        )
        deployed += position.entry_price * position.shares

    pnl = realized_pnl(fills)
    return BacktestResult(
        fills=tuple(fills),
        realized_pnl=pnl,
        roi=roi(pnl, deployed) if deployed > 0 else 0.0,
        signal_probs=tuple(signal_probs),
    )
```

- [ ] **Step 4: Run the engine tests**

Run: `uv run pytest tests/backtest/test_engine.py -v`
Expected: all PASS (new maker tests + the retained ones).

- [ ] **Step 5: Run the full suite (catch downstream breakage)**

Run: `uv run pytest`
Expected: PASS. If `tests/backtest/test_walkforward.py` or others relied on immediate fills, none do (they aggregate `BacktestResult` fields). Investigate any failure; do not skip.

- [ ] **Step 6: Lint and types**

Run: `uv run ruff check && uv run ruff format --check && uv run mypy`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add backtest/engine.py tests/backtest/test_engine.py
git commit -m "feat(backtest): engine uses the maker-first fill model"
```

---

### Task 4: Thin async orchestrator (`app/orchestrator.py`)

**Files:**
- Create: `app/orchestrator.py`
- Test: `tests/app/test_orchestrator.py`

**Interfaces:**
- Consumes: `app.feed.Feed`, `backtest.view.MarketView`, `backtest.strategy.Strategy`, `core.fills.*`, `core.metrics.realized_pnl`, `core.models.{RiskLimits, CostInputs, Fill, Side}`, `data.events.Quote`, `data.reference.ReferencePrice`.
- Produces:
  - `PaperResult(fills: tuple[Fill, ...], realized_pnl: Decimal)` (frozen dataclass).
  - `async def run_paper(feed: Feed, strategy: Strategy, limits: RiskLimits, *, reference: ReferencePrice | None = None, costs: CostInputs = ..., fill_expiry: timedelta = timedelta(minutes=5)) -> PaperResult` — consumes the async feed, builds the as-of `MarketView` per event, applies the same maker lifecycle as the engine, places **zero real orders**.

> The orchestrator mirrors the engine's lifecycle but over an async feed; both share `core.fills`. Decision logging is provided by `CompositeStrategy` (Task 8), so `run_paper` stays a pure simulation loop here.

- [ ] **Step 1: Write the failing test**

```python
# tests/app/test_orchestrator.py
import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.feed import HistoricalFeed
from app.orchestrator import PaperResult, run_paper
from backtest.view import MarketView
from core.models import (
    CalibrationSample,
    Decision,
    GateResult,
    RiskLimits,
    Side,
    SizingResult,
)
from data.events import MarketEvent, Quote, event_from_quote

_T0 = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


def _event(minute: int, price: str) -> MarketEvent:
    return event_from_quote(
        Quote(market_id="m", ts=_T0 + timedelta(minutes=minute), price=Decimal(price))
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        bankroll=Decimal("25"),
        kelly_fraction=0.25,
        max_position_fraction=0.2,
        max_position_usd=Decimal("5"),
    )


class _BuyBelowHalf:
    def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
        if event.quote.price < Decimal("0.50"):
            return Decision(
                gate=GateResult.act(side=Side.BUY_YES, edge=0.1),
                sizing=SizingResult(stake_usd=Decimal("4"), shares=Decimal("10")),
            )
        return None


def test_run_paper_matches_engine_on_historical_feed() -> None:
    async def _run() -> None:
        feed = HistoricalFeed([_event(1, "0.40"), _event(2, "0.38"), _event(3, "0.55")])
        result = await run_paper(feed, _BuyBelowHalf(), _limits())
        assert isinstance(result, PaperResult)
        assert len(result.fills) == 1
        assert result.fills[0].entry_price == Decimal("0.40")
        assert result.realized_pnl == Decimal("1.50")

    asyncio.run(_run())


def test_run_paper_no_orders_when_strategy_abstains() -> None:
    async def _run() -> None:
        class _NeverActs:
            def on_event(
                self, event: MarketEvent, view: MarketView
            ) -> Decision | None:
                return None

        feed = HistoricalFeed([_event(1, "0.40"), _event(2, "0.55")])
        result = await run_paper(feed, _NeverActs(), _limits())
        assert result.fills == ()
        assert result.realized_pnl == Decimal("0")

    asyncio.run(_run())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/app/test_orchestrator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.orchestrator'`.

- [ ] **Step 3: Write minimal implementation**

```python
# app/orchestrator.py
"""Thin async paper-trading loop. Zero real orders -- fills are simulated.

Consumes a Feed, builds the as-of MarketView per event, runs the strategy, and
applies the same maker-first fill lifecycle as the backtest engine via
core.fills. Historical feed -> deterministic; live feed -> human-run smoke test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.feed import Feed
from backtest.strategy import Strategy
from backtest.view import MarketView
from core.fills import MakerOrder, crosses, round_trip_fill_costs, token_price
from core.metrics import realized_pnl
from core.models import CostInputs, Fill, RiskLimits, Side
from data.events import Quote
from data.reference import ReferencePrice

_ZERO_COSTS = CostInputs(
    spread=Decimal(0),
    fee_rate=Decimal(0),
    gas_usd=Decimal(0),
    model_error_margin=Decimal(0),
)


@dataclass(frozen=True)
class PaperResult:
    fills: tuple[Fill, ...]
    realized_pnl: Decimal


@dataclass
class _OpenPosition:
    side: Side
    entry_price: Decimal
    shares: Decimal
    opened_ts: datetime


async def run_paper(
    feed: Feed,
    strategy: Strategy,
    limits: RiskLimits,
    *,
    reference: ReferencePrice | None = None,
    costs: CostInputs = _ZERO_COSTS,
    fill_expiry: timedelta = timedelta(minutes=5),
) -> PaperResult:
    quotes_by_market: dict[str, list[Quote]] = {}
    pending: dict[str, MakerOrder] = {}
    open_positions: dict[str, _OpenPosition] = {}
    fills: list[Fill] = []

    async for event in feed.events():
        quotes_by_market.setdefault(event.market_id, []).append(event.quote)
        view = MarketView(event.ts, quotes_by_market, reference)
        decision = strategy.on_event(event, view)

        market = event.market_id
        quote = event.quote

        if market in pending:
            order = pending[market]
            if crosses(order, quote):
                open_positions[market] = _OpenPosition(
                    side=order.side,
                    entry_price=token_price(order.side, order.limit_price),
                    shares=order.shares,
                    opened_ts=quote.ts,
                )
                del pending[market]
            elif quote.ts > order.expiry_ts:
                del pending[market]
        elif market in open_positions and quote.ts > open_positions[market].opened_ts:
            position = open_positions.pop(market)
            exit_price = token_price(position.side, quote.price)
            fills.append(
                Fill(
                    side=position.side,
                    entry_price=position.entry_price,
                    exit_price=exit_price,
                    shares=position.shares,
                    costs_usd=round_trip_fill_costs(
                        costs, position.entry_price, exit_price, position.shares
                    ),
                )
            )

        if (
            market not in pending
            and market not in open_positions
            and decision is not None
            and decision.gate.action == "act"
            and decision.gate.side is not None
            and decision.sizing.shares > 0
        ):
            pending[market] = MakerOrder(
                side=decision.gate.side,
                limit_price=quote.price,
                shares=decision.sizing.shares,
                placed_ts=quote.ts,
                expiry_ts=quote.ts + fill_expiry,
            )

    for market, position in open_positions.items():
        last_price = quotes_by_market[market][-1].price
        exit_price = token_price(position.side, last_price)
        fills.append(
            Fill(
                side=position.side,
                entry_price=position.entry_price,
                exit_price=exit_price,
                shares=position.shares,
                costs_usd=round_trip_fill_costs(
                    costs, position.entry_price, exit_price, position.shares
                ),
            )
        )

    return PaperResult(fills=tuple(fills), realized_pnl=realized_pnl(fills))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/app/test_orchestrator.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Lint, types, full suite**

Run: `uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest`
Expected: all clean/PASS.

- [ ] **Step 6: Commit**

```bash
git add app/orchestrator.py tests/app/test_orchestrator.py
git commit -m "feat(app): thin async paper-trading orchestrator"
```

---

### Task 5: odds-api.io payload models + reference adapter (`data/oddsapi.py`)

**Files:**
- Modify: `data/payloads.py` (add `OddsApiOdds`, `OddsApiBookmakerMarket`)
- Create: `data/oddsapi.py`
- Create: `tests/fixtures/oddsapi/odds_ml.json`
- Test: `tests/data/test_oddsapi.py`

**Interfaces:**
- Consumes: `core.signals.devig.devig`, `core.signals.devig.overround`, `data.events.Quote`, `data.http.{RateLimiter, get_json}`, `data.reference.ReferencePrice`.
- Produces:
  - `parse_ml_fair(payload: OddsApiOdds, bookmaker: str) -> ReferenceMl | None` — extracts the named bookmaker's ML home/draw/away decimals, de-vigs to fair `[home, draw, away]`, returns `ReferenceMl(fair, overround, updated_at)`; `None` if the bookmaker/market is absent or settled (empty `bookmakers`).
  - `ReferenceMl(fair: dict[str, float], overround: float, updated_at: datetime)` (frozen dataclass).
  - `RecordedReference(snapshots: Sequence[ReferenceSnapshot])` implementing `ReferencePrice.at(market_id, ts)` over self-recorded timestamped fairs (as-of: never returns a snapshot after `ts`).
  - `ReferenceSnapshot(market_id: str, ts: datetime, fair: Decimal)` (frozen).
  - `OddsApiClient(...)` async client with `fetch_odds(event_id: str, bookmakers: Sequence[str]) -> OddsApiOdds` — human-run only, never tested against the network.

> **Identity mapping is explicit config**, not auto-matched: callers map `polymarket_market_id -> (oddsapi outcome)`. The recorder (Task 6) records `(market_id, ts, fair)` snapshots so the backtest path uses `RecordedReference` while the live path uses `OddsApiClient`.

- [ ] **Step 1: Create the recorded ML odds fixture**

```json
// tests/fixtures/oddsapi/odds_ml.json
{
  "eventId": "wc-2026-eng-fra",
  "updatedAt": "2026-06-21T12:00:00Z",
  "bookmakers": {
    "Betfair Exchange": [
      {
        "name": "ML",
        "odds": [{ "home": "2.10", "draw": "3.40", "away": "3.70" }]
      }
    ],
    "Bet365": [
      {
        "name": "ML",
        "odds": [{ "home": "2.00", "draw": "3.30", "away": "3.60" }]
      }
    ]
  }
}
```

- [ ] **Step 2: Write the failing test**

```python
# tests/data/test_oddsapi.py
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from data.oddsapi import (
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/data/test_oddsapi.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'data.oddsapi'`).

- [ ] **Step 4: Add payload models**

Append to `data/payloads.py`:

```python
class OddsApiBookmakerMarket(BaseModel):
    name: str
    odds: list[dict[str, str]]


class OddsApiOdds(BaseModel):
    eventId: str
    updatedAt: datetime
    bookmakers: dict[str, list[OddsApiBookmakerMarket]] = {}
```

> Check the top of `data/payloads.py` for the existing `BaseModel` import and `from datetime import datetime` (Gamma/CLOB payloads already use pydantic). Add `from datetime import datetime` only if absent.

- [ ] **Step 5: Write the adapter**

```python
# data/oddsapi.py
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

    async def fetch_odds(
        self, event_id: str, bookmakers: Sequence[str]
    ) -> OddsApiOdds:
        raw = await get_json(
            self._client,
            "/odds",
            {"apiKey": self._key, "eventId": event_id, "bookmakers": ",".join(bookmakers), "market": "ML"},
            limiter=self._limiter,
        )
        return OddsApiOdds.model_validate(raw)
```

- [ ] **Step 6: Add an `OddsApiClient` mock-transport test**

```python
# append to tests/data/test_oddsapi.py
import asyncio

import httpx

from data.oddsapi import OddsApiClient


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
```

- [ ] **Step 7: Run tests, lint, types**

Run: `uv run pytest tests/data/test_oddsapi.py -v && uv run ruff check && uv run ruff format --check && uv run mypy`
Expected: PASS / clean.

- [ ] **Step 8: Commit**

```bash
git add data/oddsapi.py data/payloads.py tests/data/test_oddsapi.py tests/fixtures/oddsapi/odds_ml.json
git commit -m "feat(data): odds-api ML reference adapter + recorded reference"
```

---

### Task 6: Reference fixture recorder + live Gamma group glue

**Files:**
- Create: `scripts/record_reference_fixtures.py`
- Modify: `data/gamma.py` (add `fetch_event_groups`)
- Test: `tests/data/test_gamma.py` (add a `fetch_event_groups` mock-transport test)

**Interfaces:**
- Consumes: `data.oddsapi.{OddsApiClient, parse_ml_fair, ReferenceSnapshot}`, `data.gamma.GammaClient`, `data.gamma.parse_event_groups`, `data.payloads.GammaEvent`, `data.events.MarketGroup`.
- Produces:
  - `GammaClient.fetch_events(...) -> list[GammaEvent]` and `GammaClient.fetch_event_groups(...) -> list[MarketGroup]` — fetch live events and build groups via the existing pure `parse_event_groups`.
  - `scripts/record_reference_fixtures.py` — human-run tool that polls `/odds` and appends timestamped `ReferenceSnapshot` rows to a JSON file. Not in the suite.

> The recorder graduates `scripts/verify_odds_api.py` / `scripts/probe_odds_detail.py`; it self-records Betfair-Exchange history (the probe found `/odds/movements` covers only Bet365). The recorded snapshots feed Task 8's train-window calibration.

- [ ] **Step 1: Write the failing Gamma test**

Inspect `tests/data/test_gamma.py` and `tests/fixtures/gamma/events_negrisk.json` first (the event fixture already exists from Phase 3). Add:

```python
# append to tests/data/test_gamma.py
import asyncio

import httpx

from data.events import MarketGroup
from data.gamma import GammaClient


def test_fetch_event_groups_over_mock_transport() -> None:
    async def _run() -> None:
        payload = json.loads((_FIX / "events_negrisk.json").read_text())

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        async with GammaClient(
            transport=httpx.MockTransport(handler), base_url="http://t"
        ) as client:
            groups = await client.fetch_event_groups(limit=100)
        assert all(isinstance(g, MarketGroup) for g in groups)
        assert any(len(g.market_ids) >= 2 for g in groups)

    asyncio.run(_run())
```

> Match the existing imports/`_FIX` constant at the top of `tests/data/test_gamma.py`; reuse `json` / `Path` already imported there. If the `events_negrisk.json` shape is an envelope (`{"data": [...]}` or a bare list), align the handler/payload accordingly with how Phase 3 parses it.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/data/test_gamma.py::test_fetch_event_groups_over_mock_transport -v`
Expected: FAIL (`GammaClient` has no `fetch_event_groups`).

- [ ] **Step 3: Add the live group glue to `GammaClient`**

Inspect `data/payloads.py` for `GammaEvent` and the `/events` envelope shape, then add to `GammaClient` in `data/gamma.py`:

```python
    async def fetch_events(self, *, limit: int = 100) -> list[GammaEvent]:
        events: list[GammaEvent] = []
        offset = 0
        while True:
            raw = await get_json(
                self._client,
                "/events",
                {"limit": limit, "offset": offset},
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

    async def fetch_event_groups(self, *, limit: int = 100) -> list[MarketGroup]:
        groups: list[MarketGroup] = []
        for event in await self.fetch_events(limit=limit):
            groups.extend(parse_event_groups(event))
        return groups
```

> Add `from data.payloads import GammaEvent` to the imports if not already present (Phase 3 added `parse_event_groups`, so `GammaEvent` is likely imported). If the `/events` response is enveloped, unwrap it the same way `fetch_markets` handles `/markets`.

- [ ] **Step 4: Run the Gamma test**

Run: `uv run pytest tests/data/test_gamma.py -v`
Expected: PASS.

- [ ] **Step 5: Write the recorder script (human-run, not tested)**

```python
# scripts/record_reference_fixtures.py
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
            {"market_id": market_id, "ts": ts, "fair": str(Decimal(str(ref.fair[outcome])))}
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
```

- [ ] **Step 6: Verify the script imports and the suite stays green**

Run: `uv run python -c "import ast; ast.parse(open('scripts/record_reference_fixtures.py').read())" && uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest`
Expected: clean / PASS (the recorder is import-checked and lint/type-checked but not executed — it needs network).

- [ ] **Step 7: Commit**

```bash
git add data/gamma.py scripts/record_reference_fixtures.py tests/data/test_gamma.py
git commit -m "feat(data): live Gamma group fetch + reference fixture recorder"
```

---

### Task 7: Promotion flag + composite precedence strategy (`backtest/signals.py`)

**Files:**
- Modify: `backtest/signals.py` (add `NamedSignal`, `SignalDecision`, `CompositeStrategy`)
- Test: `tests/backtest/test_composite.py`

**Interfaces:**
- Consumes: `backtest.strategy.Strategy`, `backtest.view.MarketView`, `core.models.{Decision, Side}`, `data.events.MarketEvent`.
- Produces:
  - `NamedSignal(source: str, strategy: Strategy, promoted: bool)` (frozen dataclass) — the explicit promotion flag carried per signal.
  - `SignalDecision(source: str, market_id: str, ts: datetime, action: str, side: Side | None, p_fair: float | None, promoted: bool, agreement: bool)` (frozen).
  - `CompositeStrategy(signals: Sequence[NamedSignal], log: list[SignalDecision] | None = None)` implementing `Strategy`. `on_event` evaluates every sub-signal in priority order, logs each `SignalDecision`, and returns the **first promoted, non-abstain** decision (or `None`). Unpromoted signals are logged but never act. Sets `agreement=True` on every record for the event when ≥2 sub-signals fired the same side.

> S2 must precede S1 must precede S3 in the `signals` sequence; the caller orders them. The promotion flag lives on each `NamedSignal`; the composer reads it. This realizes the spec's "unpromoted → log, don't act."

- [ ] **Step 1: Write the failing test**

```python
# tests/backtest/test_composite.py
from datetime import UTC, datetime
from decimal import Decimal

from backtest.signals import CompositeStrategy, NamedSignal, SignalDecision
from backtest.view import MarketView
from core.models import Decision, GateResult, Side, SizingResult
from data.events import MarketEvent, Quote, event_from_quote

_T0 = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


def _event() -> MarketEvent:
    return event_from_quote(Quote(market_id="m", ts=_T0, price=Decimal("0.50")))


def _act(side: Side, prob: float) -> Decision:
    return Decision(
        gate=GateResult.act(side=side, edge=0.1),
        sizing=SizingResult(stake_usd=Decimal("4"), shares=Decimal("8")),
        prob=prob,
    )


def _abstain() -> Decision:
    return Decision(
        gate=GateResult.abstain(reason="aligned"),
        sizing=SizingResult(stake_usd=Decimal("0"), shares=Decimal("0")),
        prob=0.5,
    )


class _Fixed:
    def __init__(self, decision: Decision | None) -> None:
        self._decision = decision

    def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
        return self._decision


def _view() -> MarketView:
    return MarketView(_T0, {"m": [_event().quote]}, None)


def test_precedence_first_promoted_acting_wins() -> None:
    log: list[SignalDecision] = []
    composite = CompositeStrategy(
        [
            NamedSignal("S2", _Fixed(_abstain()), promoted=True),
            NamedSignal("S1", _Fixed(_act(Side.BUY_YES, 0.7)), promoted=True),
            NamedSignal("S3", _Fixed(_act(Side.BUY_NO, 0.2)), promoted=False),
        ],
        log=log,
    )
    decision = composite.on_event(_event(), _view())
    assert decision is not None and decision.gate.side is Side.BUY_YES  # S1 wins
    assert [r.source for r in log] == ["S2", "S1", "S3"]


def test_unpromoted_signal_logs_but_never_acts() -> None:
    log: list[SignalDecision] = []
    composite = CompositeStrategy(
        [
            NamedSignal("S2", _Fixed(_abstain()), promoted=True),
            NamedSignal("S3", _Fixed(_act(Side.BUY_YES, 0.9)), promoted=False),
        ],
        log=log,
    )
    assert composite.on_event(_event(), _view()) is None  # S3 cannot act
    s3 = next(r for r in log if r.source == "S3")
    assert s3.action == "act" and s3.promoted is False


def test_agreement_flag_set_when_two_signals_share_side() -> None:
    log: list[SignalDecision] = []
    composite = CompositeStrategy(
        [
            NamedSignal("S2", _Fixed(_act(Side.BUY_YES, 0.7)), promoted=True),
            NamedSignal("S1", _Fixed(_act(Side.BUY_YES, 0.65)), promoted=True),
        ],
        log=log,
    )
    composite.on_event(_event(), _view())
    assert all(r.agreement for r in log)


def test_all_abstain_returns_none() -> None:
    composite = CompositeStrategy(
        [NamedSignal("S2", _Fixed(_abstain()), promoted=True), NamedSignal("S1", _Fixed(None), promoted=True)]
    )
    assert composite.on_event(_event(), _view()) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/backtest/test_composite.py -v`
Expected: FAIL (`ImportError: cannot import name 'CompositeStrategy'`).

- [ ] **Step 3a: Extend the top-of-file imports (no mid-file imports — `ruff` E402/F811)**

`backtest/signals.py` already imports (lines 9-20) `Sequence`, `Decimal`, `MarketView`, `Calibrator`, `evaluate`, `CostInputs, Decision, RiskLimits, TradeCandidate`, `scan_consistency`, `divergence`, `MarketEvent, MarketGroup`. Add only the genuinely new names **into the existing top block**, keeping isort order:
- add `from dataclasses import dataclass`
- add `from datetime import datetime`
- add `from backtest.strategy import Strategy`
- change `from core.models import CostInputs, Decision, RiskLimits, TradeCandidate` → `from core.models import CostInputs, Decision, RiskLimits, Side, TradeCandidate`

Do **not** re-import `Decision`, `MarketEvent`, `MarketView`, or `Sequence` (already present).

- [ ] **Step 3b: Append the class definitions (no imports in this block)**

Append to `backtest/signals.py` (keep the existing `DivergenceStrategy` / `ConsistencyStrategy`):

```python
@dataclass(frozen=True)
class NamedSignal:
    source: str
    strategy: Strategy
    promoted: bool


@dataclass(frozen=True)
class SignalDecision:
    source: str
    market_id: str
    ts: datetime
    action: str
    side: Side | None
    p_fair: float | None
    promoted: bool
    agreement: bool


def _acting_side(decision: Decision | None) -> Side | None:
    if decision is None or decision.gate.action != "act":
        return None
    return decision.gate.side


class CompositeStrategy:
    """Priority-precedence composer: first promoted non-abstain signal acts.

    Every sub-signal's decision is logged (for walk-forward Brier/EV evidence),
    but only a promoted signal may produce the acting decision. Unpromoted (S3
    shadow) signals are recorded, never traded.
    """

    def __init__(
        self,
        signals: Sequence[NamedSignal],
        log: list[SignalDecision] | None = None,
    ) -> None:
        self._signals = list(signals)
        self._log = log

    def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
        evaluated: list[tuple[NamedSignal, Decision | None]] = [
            (sig, sig.strategy.on_event(event, view)) for sig in self._signals
        ]
        sides = [s for _, d in evaluated if (s := _acting_side(d)) is not None]
        agreement = any(sides.count(s) >= 2 for s in sides)

        if self._log is not None:
            for sig, decision in evaluated:
                action = "act" if _acting_side(decision) is not None else "abstain"
                self._log.append(
                    SignalDecision(
                        source=sig.source,
                        market_id=event.market_id,
                        ts=event.ts,
                        action=action,
                        side=_acting_side(decision),
                        p_fair=decision.prob if decision is not None else None,
                        promoted=sig.promoted,
                        agreement=agreement,
                    )
                )

        for sig, decision in evaluated:
            if sig.promoted and _acting_side(decision) is not None:
                return decision
        return None
```

> `CompositeStrategy` uses `Sequence` and `list` — `Sequence` is already imported at the top of `backtest/signals.py`; no new typing import is needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/backtest/test_composite.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint, types, full suite**

Run: `uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest`
Expected: clean / PASS.

- [ ] **Step 6: Commit**

```bash
git add backtest/signals.py tests/backtest/test_composite.py
git commit -m "feat(backtest): composite precedence strategy with shadow logging"
```

---

### Task 8: Per-split calibration with a look-ahead guard (`backtest/calibrated.py`)

**Files:**
- Create: `backtest/calibrated.py`
- Test: `tests/backtest/test_calibrated.py`

**Interfaces:**
- Consumes: `backtest.engine.replay`, `backtest.report.calibration_samples`, `backtest.walkforward.Split`, `core.calibration.Calibrator`, `core.models.{RiskLimits, CalibrationSample}`, `data.events.MarketEvent`.
- Produces:
  - `StrategyFactory = Callable[[Calibrator], Strategy]`.
  - `fit_split_calibrator(train_events: Sequence[MarketEvent], make_strategy: StrategyFactory, outcomes: Mapping[str, int], make_calibrator: Callable[[], Calibrator], limits: RiskLimits) -> Calibrator` — runs the train window through an **identity** calibrator to recover raw `p_fair`, joins **train-window** outcomes only, fits and returns a fresh calibrator.
  - `run_walk_forward(events, splits, make_strategy, outcomes, make_calibrator, limits) -> list[BacktestResult]` — per split: fit on train, evaluate test with the fitted calibrator.

> **Look-ahead guard:** the fit input is exactly `train_result.signal_probs` (train events only). A test proves the calibrator never sees a raw `p_fair` value that occurs only in the test window. The identity-calibrator pass is how raw probabilities are recovered without exposing them through the strategy API.

> **Documented assumption (caller's responsibility):** the index-based split must not divide a single `market_id` across the train/test boundary. A market carries one resolved 0/1 outcome; if it appears in both windows, fitting on its train-window quotes uses an outcome that is only known post-resolution — subtle leakage. The caller orders events so each market's quotes fall entirely within one split window (group by match, split on match boundaries). This is a real constraint of index-based splitting, recorded here; a stricter match-aware splitter is a later refinement, not Phase 4.

- [ ] **Step 1: Write the failing test**

```python
# tests/backtest/test_calibrated.py
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from backtest.calibrated import fit_split_calibrator, run_walk_forward
from backtest.view import MarketView
from backtest.walkforward import Split
from core.models import CalibrationSample, Decision, GateResult, RiskLimits, Side, SizingResult
from data.events import MarketEvent, Quote, event_from_quote

_T0 = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


def _limits() -> RiskLimits:
    return RiskLimits(
        bankroll=Decimal("25"),
        kelly_fraction=0.25,
        max_position_fraction=0.2,
        max_position_usd=Decimal("5"),
    )


def _events(probs: Sequence[float]) -> list[MarketEvent]:
    return [
        event_from_quote(
            Quote(market_id=f"m{i}", ts=_T0 + timedelta(minutes=i), price=Decimal("0.50"))
        )
        for i, _ in enumerate(probs)
    ]


class _EmitProb:
    """Strategy double: emits a per-market p_fair as the Decision.prob."""

    def __init__(self, calibrator, probs: Mapping[str, float]) -> None:
        self._cal = calibrator
        self._probs = probs

    def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
        raw = self._probs.get(event.market_id)
        if raw is None:
            return None
        q = self._cal.predict(raw)
        return Decision(
            gate=GateResult.abstain(reason="probe"),
            sizing=SizingResult(stake_usd=Decimal("0"), shares=Decimal("0")),
            prob=q,
        )


class _SpyCalibrator:
    def __init__(self) -> None:
        self.fitted_raw: list[float] = []

    def fit(self, samples: Sequence[CalibrationSample]) -> None:
        self.fitted_raw.extend(s.raw_prob for s in samples)

    def predict(self, raw: float) -> float:
        return raw


def test_calibrator_fit_only_on_train_window_probs() -> None:
    # train markets emit 0.20; test markets emit 0.80. The fit must never see 0.80.
    probs = {"m0": 0.20, "m1": 0.20, "m2": 0.80, "m3": 0.80}
    events = _events([0.2, 0.2, 0.8, 0.8])
    outcomes: Mapping[str, int] = {"m0": 0, "m1": 0, "m2": 1, "m3": 1}
    spy = _SpyCalibrator()

    train = [e for e in events if e.market_id in ("m0", "m1")]
    fit_split_calibrator(
        train,
        make_strategy=lambda cal: _EmitProb(cal, probs),
        outcomes=outcomes,
        make_calibrator=lambda: spy,
        limits=_limits(),
    )
    assert spy.fitted_raw == [0.20, 0.20]
    assert 0.80 not in spy.fitted_raw  # no test-window leakage
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/backtest/test_calibrated.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'backtest.calibrated'`).

- [ ] **Step 3: Write the implementation**

```python
# backtest/calibrated.py
"""Walk-forward calibration: fit one calibrator per split on the TRAIN window only.

A calibrator is a fitted transform, so fitting it on test-window outcomes is
look-ahead. We recover raw p_fair by running the train window through an identity
calibrator, join train-window outcomes, fit a fresh calibrator, then evaluate the
test window with it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from backtest.engine import BacktestResult, replay
from backtest.report import calibration_samples
from backtest.strategy import Strategy
from backtest.walkforward import Split
from core.calibration import Calibrator
from core.models import CalibrationSample, RiskLimits
from data.events import MarketEvent

StrategyFactory = Callable[[Calibrator], Strategy]


class _Identity:
    def fit(self, samples: Sequence[CalibrationSample]) -> None:
        return None

    def predict(self, raw: float) -> float:
        return raw


def fit_split_calibrator(
    train_events: Sequence[MarketEvent],
    make_strategy: StrategyFactory,
    outcomes: Mapping[str, int],
    make_calibrator: Callable[[], Calibrator],
    limits: RiskLimits,
) -> Calibrator:
    raw_result = replay(train_events, make_strategy(_Identity()), limits)
    samples = calibration_samples(raw_result.signal_probs, outcomes)
    calibrator = make_calibrator()
    calibrator.fit(samples)
    return calibrator


def run_walk_forward(
    events: Sequence[MarketEvent],
    splits: Sequence[Split],
    make_strategy: StrategyFactory,
    outcomes: Mapping[str, int],
    make_calibrator: Callable[[], Calibrator],
    limits: RiskLimits,
) -> list[BacktestResult]:
    results: list[BacktestResult] = []
    for split in splits:
        train = list(events[split.train.start : split.train.stop])
        test = list(events[split.test.start : split.test.stop])
        calibrator = fit_split_calibrator(
            train, make_strategy, outcomes, make_calibrator, limits
        )
        results.append(replay(test, make_strategy(calibrator), limits))
    return results
```

- [ ] **Step 4: Add a `run_walk_forward` end-to-end test**

```python
# append to tests/backtest/test_calibrated.py
from backtest.engine import BacktestResult


def test_run_walk_forward_returns_one_result_per_split() -> None:
    probs = {f"m{i}": 0.5 for i in range(4)}
    events = _events([0.5] * 4)
    outcomes: Mapping[str, int] = {f"m{i}": i % 2 for i in range(4)}
    splits = [Split(train=range(0, 2), test=range(2, 4))]
    results = run_walk_forward(
        events,
        splits,
        make_strategy=lambda cal: _EmitProb(cal, probs),
        outcomes=outcomes,
        make_calibrator=lambda: _SpyCalibrator(),
        limits=_limits(),
    )
    assert len(results) == 1
    assert isinstance(results[0], BacktestResult)
```

- [ ] **Step 5: Run tests, lint, types**

Run: `uv run pytest tests/backtest/test_calibrated.py -v && uv run ruff check && uv run ruff format --check && uv run mypy`
Expected: PASS / clean.

- [ ] **Step 6: Commit**

```bash
git add backtest/calibrated.py tests/backtest/test_calibrated.py
git commit -m "feat(backtest): per-split walk-forward calibration with look-ahead guard"
```

---

### Task 9: `pydantic-ai` shadow S3 agent (`llm/agent.py`) + `ShadowForecastStrategy`

**Files:**
- Modify: `pyproject.toml` (add `pydantic-ai`), `uv.lock` (via `uv lock`)
- Create: `llm/agent.py`
- Modify: `backtest/signals.py` (add `ShadowForecastStrategy`)
- Test: `tests/llm/test_agent.py`, `tests/backtest/test_shadow.py`

**Interfaces:**
- Consumes: `llm.schema.HypothesisOutput`, `core.decision.evaluate`, `core.calibration.Calibrator`, `core.models.{CostInputs, RiskLimits, TradeCandidate, Decision}`, `backtest.view.MarketView`, `data.events.MarketEvent`, `pydantic.ValidationError`.
- Produces:
  - `MarketFeatures(market_id: str, yes_price: float, reference_fair: float | None)` (frozen) — the extractor's typed output handed to the agent.
  - `ModelRunner = Callable[[MarketFeatures], object]` — injectable; tests pass a plain function so no live model/network.
  - `HypothesisAgent(runner: ModelRunner)` with `hypothesize(features) -> HypothesisOutput | None` — validates runner output into `HypothesisOutput`; malformed/raised → `None` (abstain).
  - `build_pydantic_ai_runner(model: str) -> ModelRunner` — constructs a `pydantic_ai.Agent` with `output_type=HypothesisOutput`; human-run only, never tested.
  - `ShadowForecastStrategy(agent, costs, notional_hint, calibrator, limits)` implementing `Strategy` — gathers features, calls the agent, calibrates and gates via `evaluate`; returns a `Decision` (logged by the composite as **unpromoted**) or `None`.

- [ ] **Step 1: Add the dependency**

```bash
uv add pydantic-ai
```

Expected: `pyproject.toml` gains `pydantic-ai` under `[project].dependencies`; `uv.lock` updates. If `mypy` later reports missing stubs for `pydantic_ai`, add to `pyproject.toml`:

```toml
[[tool.mypy.overrides]]
module = ["pydantic_ai.*"]
ignore_missing_imports = true
```

- [ ] **Step 2: Write the failing agent test**

```python
# tests/llm/test_agent.py
from llm.agent import HypothesisAgent, MarketFeatures
from llm.schema import HypothesisOutput


def _features() -> MarketFeatures:
    return MarketFeatures(market_id="m", yes_price=0.5, reference_fair=0.6)


def test_agent_returns_valid_hypothesis() -> None:
    def runner(f: MarketFeatures) -> object:
        return HypothesisOutput(p_fair=0.6, confidence=0.7, rationale="ref higher")

    agent = HypothesisAgent(runner)
    out = agent.hypothesize(_features())
    assert out is not None and out.p_fair == 0.6


def test_agent_validates_dict_output() -> None:
    def runner(f: MarketFeatures) -> object:
        return {"p_fair": 0.55, "confidence": 0.4, "rationale": "ok"}

    assert HypothesisAgent(runner).hypothesize(_features()) == HypothesisOutput(
        p_fair=0.55, confidence=0.4, rationale="ok"
    )


def test_agent_malformed_output_returns_none_not_raise() -> None:
    def bad(f: MarketFeatures) -> object:
        return {"p_fair": 9.9, "confidence": "nope"}  # out of range / wrong type

    assert HypothesisAgent(bad).hypothesize(_features()) is None


def test_agent_runner_exception_returns_none() -> None:
    def boom(f: MarketFeatures) -> object:
        raise RuntimeError("model timeout")

    assert HypothesisAgent(boom).hypothesize(_features()) is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/llm/test_agent.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'llm.agent'`).

- [ ] **Step 4: Write the agent**

```python
# llm/agent.py
"""Shadow S3 hypothesis agent. Model-agnostic and injectable so tests never hit
a live model. Malformed or raised output yields None (abstain) -- an unpromoted
research signal must never crash the trading loop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from llm.schema import HypothesisOutput


@dataclass(frozen=True)
class MarketFeatures:
    market_id: str
    yes_price: float
    reference_fair: float | None


ModelRunner = Callable[[MarketFeatures], object]


class HypothesisAgent:
    def __init__(self, runner: ModelRunner) -> None:
        self._runner = runner

    def hypothesize(self, features: MarketFeatures) -> HypothesisOutput | None:
        try:
            raw = self._runner(features)
            if isinstance(raw, HypothesisOutput):
                return raw
            return HypothesisOutput.model_validate(raw)
        except (ValidationError, ValueError, TypeError, RuntimeError):
            return None


def build_pydantic_ai_runner(model: str) -> ModelRunner:
    """Live runner backed by pydantic-ai. Human-run; never exercised in tests."""
    from pydantic_ai import Agent

    agent: Agent[None, HypothesisOutput] = Agent(model, output_type=HypothesisOutput)

    def runner(features: MarketFeatures) -> object:
        prompt = (
            f"Market {features.market_id}: Polymarket YES={features.yes_price}, "
            f"reference fair={features.reference_fair}. Estimate the fair YES "
            "probability with confidence and a one-line rationale."
        )
        return agent.run_sync(prompt).output

    return runner
```

> If the installed `pydantic-ai` exposes `result.data` rather than `.output`, adjust the live runner accordingly (check `uv run python -c "import pydantic_ai, inspect; print(pydantic_ai.__version__)"`). The live runner is not unit-tested, so this is a human-run detail; keep the `try/except` boundary in `hypothesize` as the safety net.

- [ ] **Step 5: Run the agent test**

Run: `uv run pytest tests/llm/test_agent.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Write the failing shadow-strategy test**

```python
# tests/backtest/test_shadow.py
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from backtest.signals import ShadowForecastStrategy
from backtest.view import MarketView
from core.models import CalibrationSample, CostInputs, RiskLimits
from data.events import Quote, event_from_quote
from llm.agent import HypothesisAgent, MarketFeatures
from llm.schema import HypothesisOutput

_T0 = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


class _Id:
    def fit(self, samples: Sequence[CalibrationSample]) -> None:
        return None

    def predict(self, raw: float) -> float:
        return raw


def _costs() -> CostInputs:
    return CostInputs(
        spread=Decimal("0.01"),
        fee_rate=Decimal("0"),
        gas_usd=Decimal("0"),
        model_error_margin=Decimal("0"),
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        bankroll=Decimal("25"),
        kelly_fraction=0.25,
        max_position_fraction=0.2,
        max_position_usd=Decimal("5"),
    )


def _strategy(runner) -> ShadowForecastStrategy:
    return ShadowForecastStrategy(
        agent=HypothesisAgent(runner),
        costs=_costs(),
        notional_hint=Decimal("10"),
        calibrator=_Id(),
        limits=_limits(),
    )


def test_shadow_emits_decision_from_hypothesis() -> None:
    quote = Quote(market_id="m", ts=_T0, price=Decimal("0.50"))
    view = MarketView(_T0, {"m": [quote]}, None)
    strat = _strategy(lambda f: HypothesisOutput(p_fair=0.70, confidence=0.6, rationale="x"))
    decision = strat.on_event(event_from_quote(quote), view)
    assert decision is not None
    assert decision.gate.action == "act"  # 0.70 vs 0.50 clears the hurdle


def test_shadow_malformed_hypothesis_returns_none() -> None:
    quote = Quote(market_id="m", ts=_T0, price=Decimal("0.50"))
    view = MarketView(_T0, {"m": [quote]}, None)
    strat = _strategy(lambda f: {"p_fair": 5.0})  # invalid -> agent yields None
    assert strat.on_event(event_from_quote(quote), view) is None
```

- [ ] **Step 7: Run test to verify it fails**

Run: `uv run pytest tests/backtest/test_shadow.py -v`
Expected: FAIL (`ImportError: cannot import name 'ShadowForecastStrategy'`).

- [ ] **Step 8: Implement `ShadowForecastStrategy` in `backtest/signals.py`**

First add `from llm.agent import HypothesisAgent, MarketFeatures` to the **top-of-file** import block (isort order: it is a first-party `llm` import, grouped with the other `backtest`/`core`/`data` first-party imports). Then append the class (no mid-file imports):

```python
class ShadowForecastStrategy:
    """S3 forecast via the hypothesis agent. Always composed as UNPROMOTED:
    it produces a calibrated Decision for logging but never opens a position.
    """

    def __init__(
        self,
        *,
        agent: HypothesisAgent,
        costs: CostInputs,
        notional_hint: Decimal,
        calibrator: Calibrator,
        limits: RiskLimits,
    ) -> None:
        self._agent = agent
        self._costs = costs
        self._notional = notional_hint
        self._calibrator = calibrator
        self._limits = limits

    def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
        ref = view.reference_at(event.market_id, event.ts)
        features = MarketFeatures(
            market_id=event.market_id,
            yes_price=float(event.quote.price),
            reference_fair=float(ref) if ref is not None else None,
        )
        hypothesis = self._agent.hypothesize(features)
        if hypothesis is None:
            return None
        candidate = TradeCandidate(
            price=event.quote.price,
            raw_prob=hypothesis.p_fair,
            costs=self._costs,
            notional_hint=self._notional,
        )
        return evaluate(candidate, self._calibrator, self._limits)
```

> `evaluate`, `TradeCandidate`, `CostInputs`, `RiskLimits`, `Calibrator`, `Decision`, `MarketEvent`, `MarketView`, `Decimal` are already imported in `backtest/signals.py` from earlier tasks; do not duplicate imports.

- [ ] **Step 9: Run tests, lint, types, full suite**

Run: `uv run pytest tests/backtest/test_shadow.py tests/llm/test_agent.py -v && uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest`
Expected: PASS / clean.

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml uv.lock llm/agent.py backtest/signals.py tests/llm/test_agent.py tests/backtest/test_shadow.py
git commit -m "feat(llm): shadow S3 hypothesis agent (pydantic-ai, mocked) + strategy"
```

---

### Task 10: Per-signal metrics surfacing (`backtest/report.py`)

**Files:**
- Modify: `backtest/report.py`
- Test: `tests/backtest/test_report.py` (add per-signal tests)

**Interfaces:**
- Consumes: `backtest.signals.SignalDecision`, `core.metrics.{brier_score, calibration_curve}`, `core.models.CalibrationSample`.
- Produces:
  - `per_signal_scores(log: Sequence[SignalDecision], outcomes: Mapping[str, int], *, bins: int = 10) -> dict[str, SignalScore]` — groups the decision log by `source`, joins each signal's `p_fair` with resolved outcomes, returns a `SignalScore` (existing dataclass) per source. Includes unpromoted (S3 shadow) sources.
  - `agreement_rate(log: Sequence[SignalDecision]) -> float` — fraction of logged decisions flagged `agreement`.

> Reuses the existing `SignalScore`, `calibration_samples`, and `score_signals`. No promotion gate — this only surfaces evidence.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/backtest/test_report.py
from datetime import UTC, datetime

from backtest.report import agreement_rate, per_signal_scores
from backtest.signals import SignalDecision
from core.models import Side

_T = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


def _rec(source: str, mid: str, prob: float, agree: bool = False) -> SignalDecision:
    return SignalDecision(
        source=source,
        market_id=mid,
        ts=_T,
        action="abstain",
        side=None,
        p_fair=prob,
        promoted=source != "S3",
        agreement=agree,
    )


def test_per_signal_scores_groups_by_source() -> None:
    log = [
        _rec("S1", "a", 1.0),
        _rec("S1", "b", 0.0),
        _rec("S3", "a", 0.0),
        _rec("S3", "b", 1.0),
    ]
    outcomes = {"a": 1, "b": 0}
    scores = per_signal_scores(log, outcomes, bins=2)
    assert set(scores) == {"S1", "S3"}
    assert scores["S1"].brier == 0.0  # perfect
    assert scores["S3"].brier == 1.0  # perfectly wrong


def test_per_signal_scores_skips_sources_without_labeled_outcomes() -> None:
    log = [_rec("S1", "z", 0.5)]
    assert per_signal_scores(log, {}, bins=2) == {}


def test_agreement_rate() -> None:
    log = [_rec("S1", "a", 0.5, agree=True), _rec("S2", "a", 0.5, agree=False)]
    assert agreement_rate(log) == 0.5
    assert agreement_rate([]) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/backtest/test_report.py -v`
Expected: the three new tests FAIL (`ImportError: cannot import name 'per_signal_scores'`).

- [ ] **Step 3: Implement in `backtest/report.py`**

Append:

```python
from backtest.signals import SignalDecision


def per_signal_scores(
    log: Sequence[SignalDecision],
    outcomes: Mapping[str, int],
    *,
    bins: int = 10,
) -> dict[str, SignalScore]:
    by_source: dict[str, list[tuple[str, float]]] = {}
    for rec in log:
        if rec.p_fair is None:
            continue
        by_source.setdefault(rec.source, []).append((rec.market_id, rec.p_fair))
    scores: dict[str, SignalScore] = {}
    for source, probs in by_source.items():
        samples = calibration_samples(probs, outcomes)
        if samples:
            scores[source] = score_signals(samples, bins=bins)
    return scores


def agreement_rate(log: Sequence[SignalDecision]) -> float:
    if not log:
        return 0.0
    return sum(1 for rec in log if rec.agreement) / len(log)
```

> `Sequence`, `Mapping`, `SignalScore`, `calibration_samples`, `score_signals` are already imported/defined in `backtest/report.py`. Importing `SignalDecision` from `backtest.signals` is safe — `signals` does not import `report`, so there is no cycle.

- [ ] **Step 4: Run tests, lint, types, full suite**

Run: `uv run pytest tests/backtest/test_report.py -v && uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest`
Expected: PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add backtest/report.py tests/backtest/test_report.py
git commit -m "feat(backtest): per-signal Brier/calibration + agreement metrics"
```

---

### Task 11: End-to-end integration scenario (the Phase 4 gate)

**Files:**
- Create: `tests/integration/__init__.py`, `tests/integration/test_lag_event.py`

**Interfaces:**
- Consumes: `app.orchestrator.run_paper`, `app.feed.HistoricalFeed`, `backtest.signals.{DivergenceStrategy, ConsistencyStrategy, ShadowForecastStrategy, CompositeStrategy, NamedSignal, SignalDecision}`, `data.oddsapi.RecordedReference` / `data.reference.ReplayReference`, `llm.agent.HypothesisAgent`, `core` models.

This is the gate: a worked match with a known lag event produces the expected ACT; a no-edge match produces ABSTAIN. It exercises the full pipeline (composite → calibration via identity → gate → sizing → maker fill) end-to-end, deterministically, offline.

- [ ] **Step 1: Create the integration test package**

```python
# tests/integration/__init__.py
```

- [ ] **Step 2: Write the lag-event ACT scenario**

```python
# tests/integration/test_lag_event.py
import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.feed import HistoricalFeed
from app.orchestrator import run_paper
from backtest.signals import (
    CompositeStrategy,
    DivergenceStrategy,
    NamedSignal,
    SignalDecision,
)
from core.models import CalibrationSample, CostInputs, RiskLimits, Side
from data.events import Quote, event_from_quote
from data.reference import ReplayReference

_T0 = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


class _Id:
    def fit(self, samples: Sequence[CalibrationSample]) -> None:
        return None

    def predict(self, raw: float) -> float:
        return raw


def _costs() -> CostInputs:
    return CostInputs(
        spread=Decimal("0.01"),
        fee_rate=Decimal("0"),
        gas_usd=Decimal("0"),
        model_error_margin=Decimal("0"),
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        bankroll=Decimal("25"),
        kelly_fraction=0.25,
        max_position_fraction=0.2,
        max_position_usd=Decimal("5"),
    )


def _quote(minute: int, price: str) -> Quote:
    return Quote(market_id="m", ts=_T0 + timedelta(minutes=minute), price=Decimal(price))


def _composite(log: list[SignalDecision]) -> CompositeStrategy:
    s1 = DivergenceStrategy(
        costs=_costs(),
        notional_hint=Decimal("10"),
        calibrator=_Id(),
        limits=_limits(),
    )
    return CompositeStrategy([NamedSignal("S1", s1, promoted=True)], log=log)


def test_lag_event_produces_act_and_fills() -> None:
    async def _run() -> None:
        # Reference fair = 0.70 throughout; Polymarket lags at 0.50 then drifts up.
        ref = ReplayReference(
            [Quote(market_id="m", ts=_T0, price=Decimal("0.70"))]
        )
        events = [_quote(1, "0.50"), _quote(2, "0.49"), _quote(3, "0.68")]
        log: list[SignalDecision] = []
        feed = HistoricalFeed([event_from_quote(q) for q in events])
        result = await run_paper(
            feed, _composite(log), _limits(), reference=ref, costs=_costs()
        )
        # S1 sees 0.50 << 0.70 fair -> ACT BUY_YES; order rests at 0.50, event 2
        # dips to 0.49 -> fills; event 3 at 0.68 -> closes for a profit.
        acted = [r for r in log if r.action == "act"]
        assert acted and acted[0].side is Side.BUY_YES
        assert len(result.fills) == 1
        assert result.realized_pnl > 0

    asyncio.run(_run())


def test_no_edge_match_abstains_and_never_fills() -> None:
    async def _run() -> None:
        ref = ReplayReference([Quote(market_id="m", ts=_T0, price=Decimal("0.50"))])
        events = [_quote(1, "0.50"), _quote(2, "0.50"), _quote(3, "0.50")]
        log: list[SignalDecision] = []
        feed = HistoricalFeed([event_from_quote(q) for q in events])
        result = await run_paper(
            feed, _composite(log), _limits(), reference=ref, costs=_costs()
        )
        assert all(r.action == "abstain" for r in log)
        assert result.fills == ()
        assert result.realized_pnl == Decimal("0")

    asyncio.run(_run())
```

- [ ] **Step 3: Run the integration tests**

Run: `uv run pytest tests/integration/test_lag_event.py -v`
Expected: PASS (2 tests). If the lag-event fill does not occur, check the `fill_expiry` default (5 min) against the event spacing (1 min) — the order placed at event 1 must still be within expiry at event 2.

- [ ] **Step 4: Run the full suite, lint, types**

Run: `uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest`
Expected: all clean / PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/__init__.py tests/integration/test_lag_event.py
git commit -m "test(integration): end-to-end lag-event ACT and no-edge ABSTAIN"
```

---

### Task 12: Update `PLAN.md` status

**Files:**
- Modify: `PLAN.md`

- [ ] **Step 1: Mark Phase 4 complete in the status list**

Change the Status section line:

```markdown
- [ ] Phase 4 — Assembly & paper trading
```

to:

```markdown
- [x] Phase 4 — Assembly & paper trading
```

- [ ] **Step 2: Verify the full gate one final time**

Run: `uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest --cov`
Expected: all clean / PASS with no network reachable from any test.

- [ ] **Step 3: Commit**

```bash
git add PLAN.md
git commit -m "docs: mark Phase 4 complete"
```

---

## Self-Review

**Spec coverage:**
- LLM agent silent S3 infra (1A) → Tasks 9 (agent, mocked, malformed-safe) + 7 (composed unpromoted) + 11 (gate).
- Composition precedence S2>S1>S3 (2A) → Task 7 (`CompositeStrategy`).
- Maker-first fill-if-crossed (3A) → Tasks 1 (pure model) + 3 (engine) + 4 (orchestrator).
- Live feed adapter + mocked orchestrator (4A) → Tasks 5 (odds-api adapter) + 6 (recorder + Gamma glue) + 2/4 (feeds/orchestrator).
- Defer promotion, surface metrics (5A) → Task 10 (per-signal Brier/calibration/EV, agreement; no gate).
- Per-split train-only calibration + look-ahead test (6) → Task 8.
- Thin async orchestrator + feed seam (7A) → Tasks 2 + 4.
- Cross-cutting: S3 shadow → Tasks 7/9; decision-time vs fill-window guard → Tasks 1 (note) + 3 (forward quotes not via MarketView) + 8 (calibration guard); agreement logging → Tasks 7/10.

**Placeholder scan:** No "TBD"/"implement later"/"handle edge cases" left; every code step shows complete code; every test step shows assertions. Notes that say "inspect existing file/shape" (Gamma `/events` envelope, payload imports, pydantic-ai `.output` vs `.data`) are pointers to verify real, existing shapes — not deferred work.

**Type consistency:** `MakerOrder`/`crosses`/`token_price`/`round_trip_fill_costs` are defined in Task 1 and reused verbatim in Tasks 3/4. `SignalDecision` fields are defined in Task 7 and consumed identically in Tasks 10/11. `HypothesisAgent.hypothesize -> HypothesisOutput | None`, `MarketFeatures`, and `ModelRunner` are defined in Task 9 and used in the shadow strategy. `Split.train`/`.test` are `range` objects (`.start`/`.stop`) per `backtest/walkforward.py`. `BacktestResult.signal_probs` (Task 3) feeds `calibration_samples` (Task 8). `run_paper` and `replay` share the `fill_expiry`/`costs` keyword defaults.

**Known behavior change flagged:** Task 3 rewrites two Phase-2 engine tests that assumed frictionless immediate fills; this is the planned Phase-4 evolution (the Phase-2 engine docstring named realistic fills as Phase 4), not test-gaming.
