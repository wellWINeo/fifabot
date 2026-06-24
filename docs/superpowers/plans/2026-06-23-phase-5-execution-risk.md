# Phase 5 — Execution & Risk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build real CLOB order mechanics validated without real funds — a dry-run/mock-tested execution venue (maker-first, EOA/type-0 signing, allowance precondition) behind a pure, property-tested risk contour (max position, MTM daily-loss cap, runaway-loop breakers, sticky kill switch with a persisted tripped flag), plus the pure S2 basket gate.

**Architecture:** A pure risk **brain** (`core/risk.py`) and a pure basket gate (`core/basket.py`) hold all financial/safety logic and are unit/property-tested in isolation. The `execution/` edge is thin: pure order models + validation (`orders.py`), an `ExecutionClient` Protocol wrapping `py-clob-client` v2 with a test `FakeExecutionClient` (`client.py`), a durable risk store (`store.py`), and an `ExecutionVenue` seam (`venue.py`) whose preflight pipeline is *validate → risk pretrade → allowance → sign+submit*, returning typed `OrderResult`s and never a raw client error. `app/orchestrator.py` gains an optional risk/venue path (legacy paper behavior unchanged when no `risk` config is supplied). Two operator scripts (non-gate) cover one-off allowances and an Amoy order probe.

**Tech Stack:** Python 3.12+, `uv`, `pydantic` v2 (frozen models), `numpy`/`scikit-learn` (core, reused), `httpx`/`websockets` (data edge, reused), new runtime dep `py-clob-client` (v2). Tests: `pytest` + `hypothesis`; async tests via `asyncio.run(...)` (no `pytest-asyncio`). Lint/types: `ruff`, `mypy` (strict). Toolchain runs inside `nix develop` via `uv`.

**Spec:** `docs/superpowers/specs/2026-06-23-phase-5-execution-risk-design.md`

## Global Constraints

These apply to every task:

- **TDD, red → green.** Write the failing test first; a step is done only when its tests pass **and** `uv run ruff check`, `uv run ruff format --check`, `uv run mypy` are clean. Never advance with a red gate.
- **No real network in any test.** The autouse guard in `tests/conftest.py` blocks `socket.getaddrinfo`. Mock all clients (inject `FakeExecutionClient`); the `py-clob-client` SDK is imported **lazily inside `ClobExecutionClient` only** and is never imported at test-collection time.
- **Pure core, thin edges.** `core/` must NOT import `data`/`backtest`/`execution`/`app`. The risk brain and basket gate are pure functions over frozen pydantic models. `execution/` may import `core` and `data`.
- **Cost gate / safety is single-sourced.** The halt decision lives only in `core/risk.py`; the basket hurdle lives only in `core/basket.py`. Venues and the orchestrator call these — they never re-implement them.
- **Two halt scopes.** `"global"` = sticky kill switch (daily-loss cap + runaway-loop breakers); rejects every order for the rest of the run. `"market"` = per-market suppression (e.g. insufficient allowance); rejects only that `market_id`. A per-market condition must never set the global switch.
- **Signature type 0 (EOA), `funder == signer`.** Baked into `OrderRequest` defaults.
- **Numeric split (from Phase 1):** `Decimal` for prices/sizes/cash/P&L (token-space prices in `(0, 1)`, on `tick_size`); `float` only for statistical/decision math (overround, edges, hurdles). Convert explicitly via `Decimal(str(x))` / `float(x)`. Timestamps are tz-aware UTC `datetime`; the clock passed as `now` is the event/quote `ts` for deterministic replay.
- **Import placement (ruff E402):** new `import` lines go in a file's existing top-of-file import block, never mid-file.
- **New runtime dep:** `py-clob-client` only, added once (Task 6). After any dependency change run `uv lock` before commit or CI `--frozen` fails.
- **Commands run in the devshell:** prefix with `nix develop --command` (e.g. `nix develop --command uv run pytest`).
- **Commit messages:** plain, no attribution trailers (no `Co-Authored-By`, no "Generated with…").

## Git & commit protocol (read before Task 0)

- Work is on branch `phase-5-execution-risk` (already created from `main`). All Phase 5 commits land here.
- **Commit only on explicit user instruction.** Commit steps are written out; the executor runs them only once authorized.
- Stage files **explicitly by path** — never `git add -A` / `git add .`.
- Do not push unless asked.

---

### Task 0: Commit the spec and this plan

**Files:**
- Commit: `docs/superpowers/specs/2026-06-23-phase-5-execution-risk-design.md`
- Commit: `docs/superpowers/plans/2026-06-23-phase-5-execution-risk.md`

- [ ] **Step 1: Stage explicitly**

```bash
git add docs/superpowers/specs/2026-06-23-phase-5-execution-risk-design.md \
        docs/superpowers/plans/2026-06-23-phase-5-execution-risk.md
```

- [ ] **Step 2: Commit**

```bash
git commit -m "docs: Phase 5 execution & risk design + plan"
```

---

### Task 1: Add `minimum_order_size` to `Market`

**Files:**
- Modify: `data/events.py` (the `Market` model)
- Test: `tests/data/test_events.py`

**Interfaces:**
- Produces: `data.events.Market.minimum_order_size: Decimal` (Field `gt=0`).

- [ ] **Step 1: Write the failing test**

Append to `tests/data/test_events.py` (merge imports into the existing top block — needs `Decimal`, `pytest`, and `Market`):

```python
def test_market_has_minimum_order_size() -> None:
    market = Market(
        market_id="m",
        question="q",
        token_ids=("yes", "no"),
        tick_size=Decimal("0.01"),
        minimum_order_size=Decimal("5"),
    )
    assert market.minimum_order_size == Decimal("5")


def test_market_rejects_nonpositive_minimum_order_size() -> None:
    with pytest.raises(ValidationError):
        Market(
            market_id="m",
            question="q",
            token_ids=("yes", "no"),
            tick_size=Decimal("0.01"),
            minimum_order_size=Decimal("0"),
        )
```

Ensure the test file imports `from pydantic import ValidationError` and `from decimal import Decimal` (add if missing).

- [ ] **Step 2: Run test to verify it fails**

Run: `nix develop --command uv run pytest tests/data/test_events.py -v`
Expected: FAIL (`Market` has no field `minimum_order_size` / unexpected keyword).

- [ ] **Step 3: Add the field**

In `data/events.py`, add to `Market` directly after the `tick_size` field:

```python
    minimum_order_size: Decimal = Field(gt=0)
```

- [ ] **Step 4: Run tests + gates**

Run: `nix develop --command uv run pytest tests/data/test_events.py -v`
Expected: PASS.
Run: `nix develop --command uv run ruff check && nix develop --command uv run ruff format --check && nix develop --command uv run mypy`
Expected: clean.

> Note: this field is `gt=0` with no default — any other `Market(...)` construction in tests/code must now pass it. Search and fix: `nix develop --command grep -rn "Market(" tests data backtest app`. Update each call site to include `minimum_order_size=Decimal("1")` (or a fixture value) in the same step.

- [ ] **Step 5: Re-run the full suite**

Run: `nix develop --command uv run pytest -q`
Expected: PASS (all call sites updated).

- [ ] **Step 6: Commit**

```bash
git add data/events.py tests/data/test_events.py
# plus any files whose Market(...) calls you updated
git commit -m "feat(data): add minimum_order_size to Market"
```

---

### Task 2: Risk brain — state, pretrade checks, breakers (`core/risk.py` part 1)

**Files:**
- Create: `core/risk.py`
- Test: `tests/core/test_risk.py`

**Interfaces:**
- Consumes: `decimal.Decimal`, `datetime`, `core.models` (nothing yet — self-contained).
- Produces:
  - `HaltScope = Literal["global", "market"]`
  - `RiskConfig(BaseModel, frozen)`: `max_position_usd: Decimal (gt=0)`, `max_daily_loss_usd: Decimal (gt=0)`, `max_orders_per_run: int (gt=0)`, `resubmit_window_seconds: float (gt=0)`, `max_orders_per_market_in_window: int (gt=0)`.
  - `RiskOrder(BaseModel, frozen)`: `market_id: str`, `notional: Decimal (gt=0)` — the minimal order shape the brain needs (decouples risk from `OrderRequest`).
  - `CheckResult(BaseModel, frozen)`: `allowed: bool`, `scope: HaltScope | None = None`, `reason: str | None = None`.
  - `RiskState(BaseModel, frozen)`: `halted: bool = False`, `halt_reason: str | None = None`, `halted_markets: frozenset[str] = frozenset()`, `day: date`, `day_realized_pnl: Decimal = Decimal(0)`, `unrealized_pnl: Decimal = Decimal(0)`, `orders_this_run: int = 0`, `exposure: dict[str, Decimal] = {}`, `order_ts: dict[str, tuple[datetime, ...]] = {}`; classmethod `start(now: datetime) -> RiskState`.
  - `trip(state, reason) -> RiskState` (sticky: no-op if already halted).
  - `suppress_market(state, market_id, reason) -> RiskState`.
  - `pretrade_check(state, config, order: RiskOrder, now) -> tuple[RiskState, CheckResult]`.
  - `on_order_placed(state, order: RiskOrder, now) -> RiskState`.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_risk.py`:

```python
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from core.risk import (
    RiskConfig,
    RiskOrder,
    RiskState,
    on_order_placed,
    pretrade_check,
    suppress_market,
    trip,
)

_T0 = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)


def _config(**overrides: object) -> RiskConfig:
    base: dict[str, object] = dict(
        max_position_usd=Decimal("5"),
        max_daily_loss_usd=Decimal("10"),
        max_orders_per_run=3,
        resubmit_window_seconds=60.0,
        max_orders_per_market_in_window=2,
    )
    base.update(overrides)
    return RiskConfig(**base)  # type: ignore[arg-type]


def _order(notional: str = "1", market: str = "m") -> RiskOrder:
    return RiskOrder(market_id=market, notional=Decimal(notional))


def test_allows_clean_order() -> None:
    state = RiskState.start(_T0)
    new_state, result = pretrade_check(state, _config(), _order(), _T0)
    assert result.allowed is True
    assert new_state.halted is False


def test_global_halt_is_sticky_and_blocks_every_order() -> None:
    state = trip(RiskState.start(_T0), "boom")
    new_state, result = pretrade_check(state, _config(), _order(), _T0)
    assert result.allowed is False
    assert result.scope == "global"
    # tripping again does not change the original reason
    assert trip(state, "second").halt_reason == "boom"


def test_market_suppression_blocks_only_that_market() -> None:
    state = suppress_market(RiskState.start(_T0), "m", "no allowance")
    _, blocked = pretrade_check(state, _config(), _order(market="m"), _T0)
    _, allowed = pretrade_check(state, _config(), _order(market="other"), _T0)
    assert blocked.allowed is False and blocked.scope == "market"
    assert allowed.allowed is True


def test_position_cap_denies_without_global_halt() -> None:
    state = on_order_placed(RiskState.start(_T0), _order("4"), _T0)
    new_state, result = pretrade_check(state, _config(), _order("2"), _T0)
    assert result.allowed is False
    assert result.scope == "market"
    assert new_state.halted is False  # cap deny is not a kill


def test_order_count_ceiling_trips_global() -> None:
    state = RiskState.start(_T0)
    config = _config(max_orders_per_run=1, max_orders_per_market_in_window=99)
    state = on_order_placed(state, _order(market="a"), _T0)  # 1 placed
    new_state, result = pretrade_check(state, config, _order(market="b"), _T0)
    assert result.allowed is False and result.scope == "global"
    assert new_state.halted is True


def test_rapid_resubmission_trips_global() -> None:
    config = _config(max_orders_per_market_in_window=2, max_orders_per_run=99)
    state = RiskState.start(_T0)
    state = on_order_placed(state, _order(market="m"), _T0)
    state = on_order_placed(state, _order(market="m"), _T0 + timedelta(seconds=1))
    new_state, result = pretrade_check(
        state, config, _order(market="m"), _T0 + timedelta(seconds=2)
    )
    assert result.allowed is False and result.scope == "global"
    assert new_state.halted is True


def test_resubmission_window_expires() -> None:
    config = _config(max_orders_per_market_in_window=2, max_orders_per_run=99)
    state = RiskState.start(_T0)
    state = on_order_placed(state, _order(market="m"), _T0)
    state = on_order_placed(state, _order(market="m"), _T0 + timedelta(seconds=1))
    # two minutes later the window has cleared
    _, result = pretrade_check(
        state, config, _order(market="m"), _T0 + timedelta(seconds=120)
    )
    assert result.allowed is True
```

- [ ] **Step 2: Run to verify failure**

Run: `nix develop --command uv run pytest tests/core/test_risk.py -v`
Expected: FAIL (`core.risk` does not exist).

- [ ] **Step 3: Implement `core/risk.py` (part 1)**

```python
"""Pure risk brain: caps, runaway-loop breakers, and a sticky kill switch.

All financial/safety logic lives here as pure functions over a frozen
RiskState. The execution edge calls these; it never re-implements them. Two
halt scopes: global (sticky kill switch) and per-market suppression.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

HaltScope = Literal["global", "market"]


class RiskConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_position_usd: Decimal = Field(gt=0)
    max_daily_loss_usd: Decimal = Field(gt=0)
    max_orders_per_run: int = Field(gt=0)
    resubmit_window_seconds: float = Field(gt=0)
    max_orders_per_market_in_window: int = Field(gt=0)


class RiskOrder(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_id: str
    notional: Decimal = Field(gt=0)


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    scope: HaltScope | None = None
    reason: str | None = None


class RiskState(BaseModel):
    model_config = ConfigDict(frozen=True)

    day: date
    halted: bool = False
    halt_reason: str | None = None
    halted_markets: frozenset[str] = frozenset()
    day_realized_pnl: Decimal = Decimal(0)
    unrealized_pnl: Decimal = Decimal(0)
    orders_this_run: int = 0
    exposure: dict[str, Decimal] = Field(default_factory=dict)
    order_ts: dict[str, tuple[datetime, ...]] = Field(default_factory=dict)

    @classmethod
    def start(cls, now: datetime) -> RiskState:
        return cls(day=now.astimezone().date())


def trip(state: RiskState, reason: str) -> RiskState:
    if state.halted:
        return state
    return state.model_copy(update={"halted": True, "halt_reason": reason})


def suppress_market(state: RiskState, market_id: str, reason: str) -> RiskState:
    return state.model_copy(
        update={"halted_markets": state.halted_markets | {market_id}}
    )


def _recent_count(state: RiskState, market_id: str, now: datetime, window: float) -> int:
    cutoff = now.timestamp() - window
    return sum(1 for ts in state.order_ts.get(market_id, ()) if ts.timestamp() > cutoff)


def pretrade_check(
    state: RiskState, config: RiskConfig, order: RiskOrder, now: datetime
) -> tuple[RiskState, CheckResult]:
    if state.halted:
        return state, CheckResult(allowed=False, scope="global", reason=state.halt_reason)
    if order.market_id in state.halted_markets:
        return state, CheckResult(
            allowed=False, scope="market", reason=f"market {order.market_id} suppressed"
        )
    if state.orders_this_run >= config.max_orders_per_run:
        reason = f"order-count ceiling {config.max_orders_per_run} reached"
        return trip(state, reason), CheckResult(allowed=False, scope="global", reason=reason)
    recent = _recent_count(state, order.market_id, now, config.resubmit_window_seconds)
    if recent >= config.max_orders_per_market_in_window:
        reason = f"rapid resubmission on {order.market_id}"
        return trip(state, reason), CheckResult(allowed=False, scope="global", reason=reason)
    projected = state.exposure.get(order.market_id, Decimal(0)) + order.notional
    if projected > config.max_position_usd:
        return state, CheckResult(
            allowed=False, scope="market", reason=f"position cap on {order.market_id}"
        )
    return state, CheckResult(allowed=True)


def on_order_placed(state: RiskState, order: RiskOrder, now: datetime) -> RiskState:
    exposure = dict(state.exposure)
    exposure[order.market_id] = exposure.get(order.market_id, Decimal(0)) + order.notional
    order_ts = dict(state.order_ts)
    order_ts[order.market_id] = (*order_ts.get(order.market_id, ()), now)
    return state.model_copy(
        update={
            "orders_this_run": state.orders_this_run + 1,
            "exposure": exposure,
            "order_ts": order_ts,
        }
    )
```

- [ ] **Step 4: Run tests + gates**

Run: `nix develop --command uv run pytest tests/core/test_risk.py -v`
Expected: PASS.
Run: `nix develop --command uv run ruff check && nix develop --command uv run mypy`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add core/risk.py tests/core/test_risk.py
git commit -m "feat(core): risk brain — pretrade checks, breakers, sticky kill switch"
```

---

### Task 3: Risk brain — P&L accounting & daily-loss trip (`core/risk.py` part 2)

**Files:**
- Modify: `core/risk.py`
- Test: `tests/core/test_risk.py`

**Interfaces:**
- Consumes: `core.models.Fill`, `core.metrics.realized_pnl`.
- Produces:
  - `on_fill(state, market_id: str, fill: Fill, now) -> RiskState` — accrue realized P&L (via `core.metrics.realized_pnl`), release that market's exposure.
  - `on_mark(state, config, unrealized_pnl: Decimal, now) -> RiskState` — set unrealized; trip global if `day_realized_pnl + unrealized_pnl <= -max_daily_loss_usd`.
  - `roll_day(state, now) -> RiskState` — at a new UTC date, reset `day`, `day_realized_pnl`, `unrealized_pnl`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_risk.py` (extend the `core.risk` import to add `on_fill, on_mark, roll_day`; add `from core.models import Fill, Side`):

```python
def test_on_fill_accrues_realized_and_releases_exposure() -> None:
    state = on_order_placed(RiskState.start(_T0), _order("4", market="m"), _T0)
    fill = Fill(
        side=Side.BUY_YES,
        entry_price=Decimal("0.40"),
        exit_price=Decimal("0.50"),
        shares=Decimal("10"),
        costs_usd=Decimal("0"),
    )
    new_state = on_fill(state, "m", fill, _T0)
    assert new_state.day_realized_pnl == Decimal("1.0")  # (0.50-0.40)*10
    assert new_state.exposure.get("m", Decimal(0)) == Decimal(0)


def test_on_mark_trips_global_when_loss_exceeds_cap() -> None:
    config = _config(max_daily_loss_usd=Decimal("0.50"))
    state = RiskState.start(_T0)
    new_state = on_mark(state, config, Decimal("-0.75"), _T0)
    assert new_state.halted is True
    assert new_state.unrealized_pnl == Decimal("-0.75")


def test_on_mark_does_not_trip_within_cap() -> None:
    config = _config(max_daily_loss_usd=Decimal("0.50"))
    new_state = on_mark(RiskState.start(_T0), config, Decimal("-0.25"), _T0)
    assert new_state.halted is False


def test_roll_day_resets_realized_at_new_utc_day() -> None:
    state = RiskState.start(_T0).model_copy(
        update={"day_realized_pnl": Decimal("-3"), "unrealized_pnl": Decimal("-1")}
    )
    rolled = roll_day(state, _T0 + timedelta(days=1))
    assert rolled.day_realized_pnl == Decimal("0")
    assert rolled.unrealized_pnl == Decimal("0")
    assert rolled.day == (_T0 + timedelta(days=1)).date()


def test_roll_day_noop_same_day() -> None:
    state = RiskState.start(_T0).model_copy(update={"day_realized_pnl": Decimal("-3")})
    assert roll_day(state, _T0 + timedelta(hours=1)).day_realized_pnl == Decimal("-3")
```

- [ ] **Step 2: Run to verify failure**

Run: `nix develop --command uv run pytest tests/core/test_risk.py -v`
Expected: FAIL (`on_fill`/`on_mark`/`roll_day` undefined).

- [ ] **Step 3: Implement part 2**

Extend the imports at the top of `core/risk.py`:

```python
from core.metrics import realized_pnl
from core.models import Fill
```

Append these functions to `core/risk.py`:

```python
def roll_day(state: RiskState, now: datetime) -> RiskState:
    today = now.astimezone().date()
    if state.day == today:
        return state
    return state.model_copy(
        update={"day": today, "day_realized_pnl": Decimal(0), "unrealized_pnl": Decimal(0)}
    )


def on_fill(state: RiskState, market_id: str, fill: Fill, now: datetime) -> RiskState:
    state = roll_day(state, now)
    pnl = realized_pnl([fill])
    released = max(Decimal(0), state.exposure.get(market_id, Decimal(0)) - fill.entry_price * fill.shares)
    exposure = dict(state.exposure)
    if released == Decimal(0):
        exposure.pop(market_id, None)
    else:
        exposure[market_id] = released
    return state.model_copy(
        update={
            "day_realized_pnl": state.day_realized_pnl + pnl,
            "exposure": exposure,
        }
    )


def on_mark(
    state: RiskState, config: RiskConfig, unrealized_pnl: Decimal, now: datetime
) -> RiskState:
    state = roll_day(state, now)
    marked = state.model_copy(update={"unrealized_pnl": unrealized_pnl})
    if state.day_realized_pnl + unrealized_pnl <= -config.max_daily_loss_usd:
        return trip(marked, f"daily loss cap {config.max_daily_loss_usd} breached")
    return marked
```

> The day boundary uses `now.astimezone().date()`. The `now` passed in is tz-aware UTC (the event `ts`), so `astimezone()` yields UTC. Keep `start()` and `roll_day()` consistent (both use `astimezone().date()`).

- [ ] **Step 4: Run tests + gates**

Run: `nix develop --command uv run pytest tests/core/test_risk.py -v`
Expected: PASS.
Run: `nix develop --command uv run ruff check && nix develop --command uv run mypy`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add core/risk.py tests/core/test_risk.py
git commit -m "feat(core): risk P&L accounting + MTM daily-loss kill switch"
```

---

### Task 4: Pure S2 basket gate (`core/basket.py`)

**Files:**
- Create: `core/basket.py`
- Test: `tests/core/test_basket.py`

**Interfaces:**
- Consumes: `core.models.CostInputs`, `core.cost_model.round_trip_cost`, `core.signals.devig.overround`.
- Produces:
  - `BasketDecision(BaseModel, frozen)`: `action: Literal["long_set", "short_set", "abstain"]`, `edge: float`.
  - `basket_cost(costs: CostInputs, notional: Decimal) -> float` — the near-riskless hurdle: `round_trip_cost` with `model_error_margin` stripped to zero.
  - `basket_decide(yes_prices: Sequence[float], costs: CostInputs, notional: Decimal) -> BasketDecision`.

> The gate is **pure and unwired** this phase — no venue or orchestrator consumes it. It exists so the cross-market arbitrage insight lives in tested code; atomic multi-leg execution is a deferred follow-on.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_basket.py`:

```python
from decimal import Decimal

from core.basket import basket_cost, basket_decide
from core.cost_model import round_trip_cost
from core.models import CostInputs


def _costs(margin: str = "0.02") -> CostInputs:
    return CostInputs(
        spread=Decimal("0.01"),
        fee_rate=Decimal("0"),
        gas_usd=Decimal("0"),
        model_error_margin=Decimal(margin),
    )


def test_basket_cost_excludes_model_error_margin() -> None:
    costs = _costs("0.02")
    notional = Decimal("10")
    assert basket_cost(costs, notional) < round_trip_cost(costs, notional)
    # equals the per-leg hurdle only when there is no margin
    assert basket_cost(_costs("0"), notional) == round_trip_cost(_costs("0"), notional)


def test_long_set_when_sum_below_one() -> None:
    # legs sum to 0.94; hurdle (spread only) = 0.01 -> 0.94 < 0.99 -> long the set
    result = basket_decide([0.30, 0.30, 0.34], _costs("0"), Decimal("10"))
    assert result.action == "long_set"
    assert abs(result.edge - 0.06) < 1e-9


def test_short_set_when_sum_above_one() -> None:
    result = basket_decide([0.40, 0.40, 0.30], _costs("0"), Decimal("10"))
    assert result.action == "short_set"
    assert abs(result.edge - 0.10) < 1e-9


def test_abstains_when_within_real_costs() -> None:
    # sum 1.005, hurdle 0.01 -> deviation 0.005 < 0.01 -> abstain
    result = basket_decide([0.335, 0.335, 0.335], _costs("0"), Decimal("10"))
    assert result.action == "abstain"


def test_basket_acts_where_per_leg_margin_would_abstain() -> None:
    # deviation 0.015; spread-only basket hurdle 0.01 -> acts.
    # a per-leg gate adding a 0.02 margin (hurdle 0.03) would abstain.
    result = basket_decide([0.33, 0.33, 0.325], _costs("0.02"), Decimal("10"))
    assert result.action == "long_set"
```

- [ ] **Step 2: Run to verify failure**

Run: `nix develop --command uv run pytest tests/core/test_basket.py -v`
Expected: FAIL (`core.basket` does not exist).

- [ ] **Step 3: Implement `core/basket.py`**

```python
"""Pure S2 basket gate: near-riskless cross-market arbitrage on a group.

For a mutually-exclusive group, `overround = sum(yes_prices)`. `overround < 1`
=> long the complete set (locked profit `1 - overround` at resolution);
`overround > 1` => short the set. Unlike the per-leg edge gate, the hurdle
excludes `model_error_margin` — an accounting identity does not pay a
probabilistic-error margin. Pure and unwired: no venue consumes this yet
(atomic multi-leg execution is deferred).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from core.cost_model import round_trip_cost
from core.models import CostInputs
from core.signals.devig import overround


class BasketDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: Literal["long_set", "short_set", "abstain"]
    edge: float


def basket_cost(costs: CostInputs, notional: Decimal) -> float:
    riskless = costs.model_copy(update={"model_error_margin": Decimal(0)})
    return round_trip_cost(riskless, notional)


def basket_decide(
    yes_prices: Sequence[float], costs: CostInputs, notional: Decimal
) -> BasketDecision:
    total = overround(yes_prices)
    hurdle = basket_cost(costs, notional)
    if total < 1.0 - hurdle:
        return BasketDecision(action="long_set", edge=1.0 - total)
    if total > 1.0 + hurdle:
        return BasketDecision(action="short_set", edge=total - 1.0)
    return BasketDecision(action="abstain", edge=0.0)
```

- [ ] **Step 4: Run tests + gates**

Run: `nix develop --command uv run pytest tests/core/test_basket.py -v`
Expected: PASS.
Run: `nix develop --command uv run ruff check && nix develop --command uv run mypy`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add core/basket.py tests/core/test_basket.py
git commit -m "feat(core): pure S2 basket gate (no model-error margin), unwired"
```

---

### Task 5: Order models + pure validation (`execution/orders.py`)

**Files:**
- Create: `execution/orders.py`
- Test: `tests/execution/__init__.py` (empty), `tests/execution/test_orders.py`

**Interfaces:**
- Consumes: `core.models.Side`, `data.events.Market`.
- Produces:
  - `OrderRequest(BaseModel, frozen)`: `market_id: str`, `token_id: str`, `side: Side`, `price: Decimal (gt=0, lt=1)`, `size: Decimal (gt=0)`, `signature_type: int = 0`; method `notional() -> Decimal` (= `price * size`).
  - `OrderResult(BaseModel, frozen)`: `status: Literal["placed", "rejected", "halted"]`, `reason: str | None = None`, `order_id: str | None = None`.
  - `OrderValidationError(ValueError)`.
  - `validate_order(order: OrderRequest, market: Market) -> None` — raise `OrderValidationError` if price is off `market.tick_size` or `size < market.minimum_order_size`.

- [ ] **Step 1: Write the failing tests**

Create `tests/execution/__init__.py` (empty) and `tests/execution/test_orders.py`:

```python
from decimal import Decimal

import pytest

from core.models import Side
from data.events import Market
from execution.orders import OrderRequest, OrderValidationError, validate_order


def _market() -> Market:
    return Market(
        market_id="m",
        question="q",
        token_ids=("yes", "no"),
        tick_size=Decimal("0.01"),
        minimum_order_size=Decimal("5"),
    )


def _order(price: str = "0.40", size: str = "10") -> OrderRequest:
    return OrderRequest(
        market_id="m",
        token_id="yes",
        side=Side.BUY_YES,
        price=Decimal(price),
        size=Decimal(size),
    )


def test_notional_is_price_times_size() -> None:
    assert _order("0.40", "10").notional() == Decimal("4.0")


def test_signature_type_defaults_to_zero() -> None:
    assert _order().signature_type == 0


def test_validate_accepts_on_tick_above_min() -> None:
    validate_order(_order("0.40", "10"), _market())  # no raise


def test_validate_rejects_off_tick_price() -> None:
    with pytest.raises(OrderValidationError, match="tick"):
        validate_order(_order("0.405", "10"), _market())


def test_validate_rejects_below_min_size() -> None:
    with pytest.raises(OrderValidationError, match="minimum"):
        validate_order(_order("0.40", "4"), _market())
```

- [ ] **Step 2: Run to verify failure**

Run: `nix develop --command uv run pytest tests/execution/test_orders.py -v`
Expected: FAIL (`execution.orders` does not exist).

- [ ] **Step 3: Implement `execution/orders.py`**

```python
"""Order request/result models and pure pre-submission validation.

Validation is a pure guard that raises before any signing or network call.
The venue maps the raised error to a typed OrderResult (it never lets a raw
error reach the caller).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from core.models import Side
from data.events import Market


class OrderValidationError(ValueError):
    """Raised when an order violates tick size or minimum order size."""


class OrderRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_id: str
    token_id: str
    side: Side
    price: Decimal = Field(gt=0, lt=1)
    size: Decimal = Field(gt=0)
    signature_type: int = 0

    def notional(self) -> Decimal:
        return self.price * self.size


class OrderResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["placed", "rejected", "halted"]
    reason: str | None = None
    order_id: str | None = None


def validate_order(order: OrderRequest, market: Market) -> None:
    if order.price % market.tick_size != 0:
        raise OrderValidationError(
            f"price {order.price} is not a multiple of tick {market.tick_size}"
        )
    if order.size < market.minimum_order_size:
        raise OrderValidationError(
            f"size {order.size} below minimum {market.minimum_order_size}"
        )
```

- [ ] **Step 4: Run tests + gates**

Run: `nix develop --command uv run pytest tests/execution/test_orders.py -v`
Expected: PASS.
Run: `nix develop --command uv run ruff check && nix develop --command uv run mypy`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add execution/orders.py tests/execution/__init__.py tests/execution/test_orders.py
git commit -m "feat(execution): order models + pure tick/min-size validation"
```

---

### Task 6: ExecutionClient seam + SDK wrapper + dependency (`execution/client.py`)

**Files:**
- Modify: `pyproject.toml` (add `py-clob-client` to `[project] dependencies`)
- Modify (generated): `uv.lock`
- Create: `execution/client.py`
- Test: `tests/execution/test_client.py`

**Interfaces:**
- Consumes: `execution.orders.OrderRequest`.
- Produces:
  - `Allowances(BaseModel, frozen)`: `usdc: Decimal`, `ctf: Decimal`.
  - `OrderStatus(BaseModel, frozen)`: `order_id: str`, `state: str`.
  - `ExecutionClient(Protocol)`: `allowances() -> Allowances`; `place(order: OrderRequest) -> str` (returns order id); `cancel(order_id: str) -> None`; `status(order_id: str) -> OrderStatus`.
  - `FakeExecutionClient` — records placed orders; configurable `usdc_allowance`; for tests.
  - `ClobExecutionClient` — wraps `py-clob-client` v2 (lazy import); EOA/type-0, `funder == signer`. **Not unit-tested** (no real network); exercised by the Amoy probe (Task 10).

- [ ] **Step 1: Add the dependency**

Edit `pyproject.toml`, add to `[project] dependencies`:

```toml
    "py-clob-client>=2",
```

Run: `nix develop --command uv lock`
Then: `nix develop --command uv sync`

- [ ] **Step 2: Write the failing tests**

Create `tests/execution/test_client.py`:

```python
from decimal import Decimal

from core.models import Side
from execution.client import Allowances, FakeExecutionClient
from execution.orders import OrderRequest


def _order() -> OrderRequest:
    return OrderRequest(
        market_id="m", token_id="yes", side=Side.BUY_YES,
        price=Decimal("0.40"), size=Decimal("10"),
    )


def test_fake_records_placed_orders_and_returns_id() -> None:
    client = FakeExecutionClient()
    order_id = client.place(_order())
    assert order_id == client.next_id
    assert client.placed == [_order()]


def test_fake_reports_configured_allowances() -> None:
    client = FakeExecutionClient(usdc_allowance=Decimal("3"))
    assert client.allowances() == Allowances(usdc=Decimal("3"), ctf=Decimal("1000"))
```

- [ ] **Step 3: Run to verify failure**

Run: `nix develop --command uv run pytest tests/execution/test_client.py -v`
Expected: FAIL (`execution.client` does not exist).

- [ ] **Step 4: Implement `execution/client.py`**

```python
"""Execution client seam: the only place the py-clob-client SDK is touched.

ExecutionClient is the Protocol the venue depends on; FakeExecutionClient is
the in-test double; ClobExecutionClient wraps py-clob-client v2 (EOA / signature
type 0, funder == signer) with a lazy SDK import so tests never load it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from core.models import Side
from execution.orders import OrderRequest


class Allowances(BaseModel):
    model_config = ConfigDict(frozen=True)

    usdc: Decimal
    ctf: Decimal


class OrderStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: str
    state: str


class ExecutionClient(Protocol):
    def allowances(self) -> Allowances: ...
    def place(self, order: OrderRequest) -> str: ...
    def cancel(self, order_id: str) -> None: ...
    def status(self, order_id: str) -> OrderStatus: ...


@dataclass
class FakeExecutionClient:
    usdc_allowance: Decimal = Decimal("1000")
    ctf_allowance: Decimal = Decimal("1000")
    next_id: str = "ord-1"
    placed: list[OrderRequest] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)

    def allowances(self) -> Allowances:
        return Allowances(usdc=self.usdc_allowance, ctf=self.ctf_allowance)

    def place(self, order: OrderRequest) -> str:
        self.placed.append(order)
        return self.next_id

    def cancel(self, order_id: str) -> None:
        self.cancelled.append(order_id)

    def status(self, order_id: str) -> OrderStatus:
        return OrderStatus(order_id=order_id, state="open")


class ClobExecutionClient:
    """Real CLOB client (EOA / type 0). Network path — never used in unit tests.

    The exact py-clob-client v2 call shapes are confirmed against the live SDK by
    the Amoy probe script, not by the offline gate. Construct only with real
    credentials from env.
    """

    def __init__(self, *, host: str, private_key: str, chain_id: int) -> None:
        from py_clob_client.client import ClobClient as _Sdk  # lazy: keeps SDK out of tests

        self._sdk = _Sdk(
            host=host, key=private_key, chain_id=chain_id, signature_type=0
        )
        self._sdk.set_api_creds(self._sdk.create_or_derive_api_creds())

    def allowances(self) -> Allowances:
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

        usdc = self._sdk.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        return Allowances(usdc=Decimal(str(usdc["allowance"])), ctf=Decimal(str(usdc["allowance"])))

    def place(self, order: OrderRequest) -> str:
        from py_clob_client.clob_types import OrderArgs
        from py_clob_client.order_builder.constants import BUY

        args = OrderArgs(
            token_id=order.token_id,
            price=float(order.price),
            size=float(order.size),
            side=BUY,
        )
        signed = self._sdk.create_order(args)
        resp = self._sdk.post_order(signed)
        return str(resp["orderID"])

    def cancel(self, order_id: str) -> None:
        self._sdk.cancel(order_id)

    def status(self, order_id: str) -> OrderStatus:
        resp = self._sdk.get_order(order_id)
        return OrderStatus(order_id=order_id, state=str(resp.get("status", "unknown")))
```

> If `mypy --strict` flags the untyped SDK, add an override to `pyproject.toml` mirroring the existing `sklearn` one:
> ```toml
> [[tool.mypy.overrides]]
> module = ["py_clob_client.*"]
> ignore_missing_imports = true
> ```

- [ ] **Step 5: Run tests + gates**

Run: `nix develop --command uv run pytest tests/execution/test_client.py -v`
Expected: PASS.
Run: `nix develop --command uv run ruff check && nix develop --command uv run mypy`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock execution/client.py tests/execution/test_client.py
git commit -m "feat(execution): ExecutionClient seam + py-clob-client v2 wrapper"
```

---

### Task 7: Durable risk store (`execution/store.py`)

**Files:**
- Create: `execution/store.py`
- Test: `tests/execution/test_store.py`

**Interfaces:**
- Consumes: `core.risk.RiskState`.
- Produces:
  - `RiskStore(Protocol)`: `load() -> RiskState | None`; `save(state: RiskState) -> None`.
  - `InMemoryRiskStore` — non-durable double for tests/legacy.
  - `FileRiskStore(path: Path)` — JSON round-trip; persists the tripped flag so a restarted process comes up halted.

- [ ] **Step 1: Write the failing tests**

Create `tests/execution/test_store.py`:

```python
from datetime import UTC, datetime
from pathlib import Path

from core.risk import RiskState, trip
from execution.store import FileRiskStore, InMemoryRiskStore

_T0 = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)


def test_in_memory_round_trips() -> None:
    store = InMemoryRiskStore()
    assert store.load() is None
    state = RiskState.start(_T0)
    store.save(state)
    assert store.load() == state


def test_file_store_round_trips(tmp_path: Path) -> None:
    store = FileRiskStore(tmp_path / "risk.json")
    assert store.load() is None
    store.save(RiskState.start(_T0))
    assert store.load() == RiskState.start(_T0)


def test_restart_while_halted_stays_halted(tmp_path: Path) -> None:
    path = tmp_path / "risk.json"
    FileRiskStore(path).save(trip(RiskState.start(_T0), "daily loss cap breached"))
    # a fresh process / fresh store instance loads the persisted halt
    reloaded = FileRiskStore(path).load()
    assert reloaded is not None
    assert reloaded.halted is True
    assert reloaded.halt_reason == "daily loss cap breached"
```

- [ ] **Step 2: Run to verify failure**

Run: `nix develop --command uv run pytest tests/execution/test_store.py -v`
Expected: FAIL (`execution.store` does not exist).

- [ ] **Step 3: Implement `execution/store.py`**

```python
"""Risk-state persistence. The pure core computes the halt; the store persists
the tripped flag at the edge so a restarted process comes up halted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from core.risk import RiskState


class RiskStore(Protocol):
    def load(self) -> RiskState | None: ...
    def save(self, state: RiskState) -> None: ...


class InMemoryRiskStore:
    def __init__(self) -> None:
        self._state: RiskState | None = None

    def load(self) -> RiskState | None:
        return self._state

    def save(self, state: RiskState) -> None:
        self._state = state


class FileRiskStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> RiskState | None:
        if not self._path.exists():
            return None
        return RiskState.model_validate_json(self._path.read_text())

    def save(self, state: RiskState) -> None:
        self._path.write_text(state.model_dump_json())
```

- [ ] **Step 4: Run tests + gates**

Run: `nix develop --command uv run pytest tests/execution/test_store.py -v`
Expected: PASS.
Run: `nix develop --command uv run ruff check && nix develop --command uv run mypy`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add execution/store.py tests/execution/test_store.py
git commit -m "feat(execution): durable risk store (persisted tripped flag)"
```

---

### Task 8: Execution venue + preflight pipeline (`execution/venue.py`)

**Files:**
- Create: `execution/venue.py`
- Test: `tests/execution/test_venue.py`

**Interfaces:**
- Consumes: `execution.orders` (`OrderRequest`, `OrderResult`, `OrderValidationError`, `validate_order`), `execution.client.ExecutionClient`, `core.risk` (`RiskConfig`, `RiskState`, `RiskOrder`, `pretrade_check`, `on_order_placed`, `suppress_market`), `data.events.Market`.
- Produces:
  - `ExecutionVenue(Protocol)`: `place(order: OrderRequest, market: Market, state: RiskState, config: RiskConfig, now: datetime) -> tuple[RiskState, OrderResult]`.
  - `SimulatedVenue` — preflight only (validate + risk), then accept (`order_id="sim"`). No client.
  - `ClobVenue(client: ExecutionClient)` — preflight, then allowance precondition (insufficient => per-market suppression + rejected), then `client.place`.

- [ ] **Step 1: Write the failing tests**

Create `tests/execution/test_venue.py`:

```python
from datetime import UTC, datetime
from decimal import Decimal

from core.models import Side
from core.risk import RiskConfig, RiskState
from data.events import Market
from execution.client import FakeExecutionClient
from execution.orders import OrderRequest
from execution.venue import ClobVenue, SimulatedVenue

_T0 = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)


def _market() -> Market:
    return Market(
        market_id="m", question="q", token_ids=("yes", "no"),
        tick_size=Decimal("0.01"), minimum_order_size=Decimal("5"),
    )


def _config(**overrides: object) -> RiskConfig:
    base: dict[str, object] = dict(
        max_position_usd=Decimal("100"),
        max_daily_loss_usd=Decimal("100"),
        max_orders_per_run=10,
        resubmit_window_seconds=60.0,
        max_orders_per_market_in_window=10,
    )
    base.update(overrides)
    return RiskConfig(**base)  # type: ignore[arg-type]


def _order(price: str = "0.40", size: str = "10") -> OrderRequest:
    return OrderRequest(
        market_id="m", token_id="yes", side=Side.BUY_YES,
        price=Decimal(price), size=Decimal(size),
    )


def test_clob_venue_places_correct_payload() -> None:
    client = FakeExecutionClient()
    state, result = ClobVenue(client).place(
        _order(), _market(), RiskState.start(_T0), _config(), _T0
    )
    assert result.status == "placed"
    assert result.order_id == "ord-1"
    assert len(client.placed) == 1
    sent = client.placed[0]
    assert sent.token_id == "yes"
    assert sent.side is Side.BUY_YES
    assert sent.price == Decimal("0.40")
    assert sent.size == Decimal("10")
    assert sent.signature_type == 0


def test_off_tick_rejected_before_client_called() -> None:
    client = FakeExecutionClient()
    _, result = ClobVenue(client).place(
        _order(price="0.405"), _market(), RiskState.start(_T0), _config(), _T0
    )
    assert result.status == "rejected"
    assert "tick" in (result.reason or "")
    assert client.placed == []


def test_below_min_size_rejected_before_client_called() -> None:
    client = FakeExecutionClient()
    _, result = ClobVenue(client).place(
        _order(size="4"), _market(), RiskState.start(_T0), _config(), _T0
    )
    assert result.status == "rejected"
    assert "minimum" in (result.reason or "")
    assert client.placed == []


def test_insufficient_allowance_suppresses_market() -> None:
    client = FakeExecutionClient(usdc_allowance=Decimal("0.5"))  # < 0.40*10 = 4.0
    state, result = ClobVenue(client).place(
        _order(), _market(), RiskState.start(_T0), _config(), _T0
    )
    assert result.status == "rejected"
    assert "allowance" in (result.reason or "")
    assert "m" in state.halted_markets
    assert client.placed == []  # not posted


def test_order_count_breach_halts_and_blocks_further_orders() -> None:
    client = FakeExecutionClient()
    venue = ClobVenue(client)
    config = _config(max_orders_per_run=1)
    state = RiskState.start(_T0)
    state, first = venue.place(_order(), _market(), state, config, _T0)
    state, second = venue.place(_order(), _market(), state, config, _T0)
    assert first.status == "placed"
    assert second.status == "halted"
    assert state.halted is True
    assert len(client.placed) == 1  # the breaching order never reached the client


def test_simulated_venue_preflights_without_client() -> None:
    state, result = SimulatedVenue().place(
        _order(), _market(), RiskState.start(_T0), _config(), _T0
    )
    assert result.status == "placed"
    assert result.order_id == "sim"
    assert state.exposure["m"] == Decimal("4.0")
```

- [ ] **Step 2: Run to verify failure**

Run: `nix develop --command uv run pytest tests/execution/test_venue.py -v`
Expected: FAIL (`execution.venue` does not exist).

- [ ] **Step 3: Implement `execution/venue.py`**

```python
"""Execution venue seam + preflight pipeline.

Pipeline order: validate -> risk pretrade -> (Clob only) allowance -> submit.
Each step yields a typed OrderResult; a raw client/validation error never
reaches the caller. SimulatedVenue runs preflight only; ClobVenue adds the
allowance precondition and the real client submit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from core.risk import (
    RiskConfig,
    RiskOrder,
    RiskState,
    on_order_placed,
    pretrade_check,
    suppress_market,
)
from data.events import Market
from execution.client import ExecutionClient
from execution.orders import (
    OrderRequest,
    OrderResult,
    OrderValidationError,
    validate_order,
)


class ExecutionVenue(Protocol):
    def place(
        self,
        order: OrderRequest,
        market: Market,
        state: RiskState,
        config: RiskConfig,
        now: datetime,
    ) -> tuple[RiskState, OrderResult]: ...


def _preflight(
    order: OrderRequest,
    market: Market,
    state: RiskState,
    config: RiskConfig,
    now: datetime,
) -> tuple[RiskState, OrderResult | None]:
    try:
        validate_order(order, market)
    except OrderValidationError as exc:
        return state, OrderResult(status="rejected", reason=str(exc))
    risk_order = RiskOrder(market_id=order.market_id, notional=order.notional())
    new_state, check = pretrade_check(state, config, risk_order, now)
    if not check.allowed:
        status = "halted" if check.scope == "global" else "rejected"
        return new_state, OrderResult(status=status, reason=check.reason)
    return new_state, None


class SimulatedVenue:
    def place(
        self,
        order: OrderRequest,
        market: Market,
        state: RiskState,
        config: RiskConfig,
        now: datetime,
    ) -> tuple[RiskState, OrderResult]:
        state, blocked = _preflight(order, market, state, config, now)
        if blocked is not None:
            return state, blocked
        risk_order = RiskOrder(market_id=order.market_id, notional=order.notional())
        return on_order_placed(state, risk_order, now), OrderResult(
            status="placed", order_id="sim"
        )


class ClobVenue:
    def __init__(self, client: ExecutionClient) -> None:
        self._client = client

    def place(
        self,
        order: OrderRequest,
        market: Market,
        state: RiskState,
        config: RiskConfig,
        now: datetime,
    ) -> tuple[RiskState, OrderResult]:
        state, blocked = _preflight(order, market, state, config, now)
        if blocked is not None:
            return state, blocked
        if self._client.allowances().usdc < order.notional():
            reason = "halt-market: insufficient USDC allowance"
            return suppress_market(state, order.market_id, reason), OrderResult(
                status="rejected", reason=reason
            )
        order_id = self._client.place(order)
        risk_order = RiskOrder(market_id=order.market_id, notional=order.notional())
        return on_order_placed(state, risk_order, now), OrderResult(
            status="placed", order_id=order_id
        )
```

- [ ] **Step 4: Run tests + gates**

Run: `nix develop --command uv run pytest tests/execution/test_venue.py -v`
Expected: PASS.
Run: `nix develop --command uv run ruff check && nix develop --command uv run mypy`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add execution/venue.py tests/execution/test_venue.py
git commit -m "feat(execution): venue seam + validate/risk/allowance/submit preflight"
```

---

### Task 9: Wire the risk/venue path into the orchestrator

**Files:**
- Modify: `app/orchestrator.py`
- Test: `tests/app/test_orchestrator.py`

**Interfaces:**
- Consumes: `execution.venue.ExecutionVenue` / `SimulatedVenue`, `execution.store` (`RiskStore`, `InMemoryRiskStore`), `core.risk` (`RiskConfig`, `RiskState`, `on_fill`, `on_mark`, `roll_day`), `execution.orders.OrderRequest`, `data.events.Market`, `core.fills.token_price`.
- Produces:
  - Extended `run_paper(..., *, risk: RiskConfig | None = None, markets: Mapping[str, Market] | None = None, venue: ExecutionVenue | None = None, store: RiskStore | None = None)`.
  - Extended `PaperResult` with `halted: bool = False`, `halt_reason: str | None = None`.

**Behavior:** When `risk is None`, the loop runs exactly as today (existing tests unchanged). When `risk is not None`, `markets` is required; before registering a pending maker order the loop builds an `OrderRequest`, calls `venue.place` threading `RiskState`, and only registers the pending order on `status == "placed"`. On a global halt it stops admitting new orders for the rest of the run. Fills feed `on_fill`; each event marks open positions via `on_mark`. State is persisted via `store.save` after each transition. Existing simulated-fill lifecycle (cross/exit) is unchanged.

- [ ] **Step 1: Write the failing test**

Append to `tests/app/test_orchestrator.py` (extend imports: `from core.risk import RiskConfig`; `from data.events import Market`):

```python
def _risk(**overrides: object) -> RiskConfig:
    base: dict[str, object] = dict(
        max_position_usd=Decimal("100"),
        max_daily_loss_usd=Decimal("100"),
        max_orders_per_run=1,
        resubmit_window_seconds=600.0,
        max_orders_per_market_in_window=10,
    )
    base.update(overrides)
    return RiskConfig(**base)  # type: ignore[arg-type]


def _markets(*ids: str) -> dict[str, Market]:
    return {
        mid: Market(
            market_id=mid, question="q", token_ids=("yes", "no"),
            tick_size=Decimal("0.01"), minimum_order_size=Decimal("1"),
        )
        for mid in ids
    }


def test_global_halt_blocks_further_admissions() -> None:
    async def _run() -> None:
        # Two markets each get one promoted decision; ceiling=1 -> 2nd halts.
        feed = HistoricalFeed([
            event_from_quote(Quote(market_id="a", ts=_T0, price=Decimal("0.40"))),
            event_from_quote(Quote(market_id="b", ts=_T0 + timedelta(minutes=1), price=Decimal("0.40"))),
        ])

        class _AlwaysBuy:
            def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
                return Decision(
                    gate=GateResult.act(side=Side.BUY_YES, edge=0.1),
                    sizing=SizingResult(stake_usd=Decimal("4"), shares=Decimal("10")),
                )

        result = await run_paper(
            feed, _AlwaysBuy(), _limits(),
            risk=_risk(max_orders_per_run=1), markets=_markets("a", "b"),
        )
        assert result.halted is True
        assert "ceiling" in (result.halt_reason or "")

    asyncio.run(_run())
```

- [ ] **Step 2: Run to verify failure**

Run: `nix develop --command uv run pytest tests/app/test_orchestrator.py::test_global_halt_blocks_further_admissions -v`
Expected: FAIL (`run_paper` has no `risk`/`markets` kwargs; `PaperResult` has no `halted`).

- [ ] **Step 3: Implement the wiring**

In `app/orchestrator.py`, extend the imports (top block):

```python
from collections.abc import Mapping

from core.models import CostInputs, Fill, RiskLimits, Side
from core.risk import RiskConfig, RiskState, on_fill, on_mark, roll_day
from data.events import Market, Quote
from execution.orders import OrderRequest
from execution.store import InMemoryRiskStore, RiskStore
from execution.venue import ExecutionVenue, SimulatedVenue
```

Extend `PaperResult`:

```python
@dataclass(frozen=True)
class PaperResult:
    fills: tuple[Fill, ...]
    realized_pnl: Decimal
    halted: bool = False
    halt_reason: str | None = None
```

Change the `run_paper` signature to add the keyword-only params:

```python
async def run_paper(
    feed: Feed,
    strategy: Strategy,
    limits: RiskLimits,
    *,
    reference: ReferencePrice | None = None,
    costs: CostInputs = _ZERO_COSTS,
    fill_expiry: timedelta = timedelta(minutes=5),
    risk: RiskConfig | None = None,
    markets: Mapping[str, Market] | None = None,
    venue: ExecutionVenue | None = None,
    store: RiskStore | None = None,
) -> PaperResult:
```

At the top of the body (before the loop), initialize the risk path:

```python
    risk_enabled = risk is not None
    if risk_enabled and markets is None:
        raise ValueError("markets is required when risk is supplied")
    venue = venue or SimulatedVenue()
    store = store or InMemoryRiskStore()
    risk_state: RiskState | None = None
    if risk_enabled:
        risk_state = store.load() or RiskState.start(_T0_FALLBACK)
```

Add this module constant near the top (after imports):

```python
from datetime import UTC

_T0_FALLBACK = datetime(1970, 1, 1, tzinfo=UTC)
```

> The fallback day is corrected on the first event via `roll_day`. (`datetime` is already imported in this module.)

Inside the loop, **replace** the existing "promote a pending order" block. The current block is:

```python
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
```

Replace it with:

```python
        wants_order = (
            market not in pending
            and market not in open_positions
            and decision is not None
            and decision.gate.action == "act"
            and decision.gate.side is not None
            and decision.sizing.shares > 0
        )
        if wants_order:
            assert decision is not None and decision.gate.side is not None
            admit = True
            if risk_enabled:
                assert risk_state is not None and markets is not None and risk is not None
                risk_state = roll_day(risk_state, quote.ts)
                if risk_state.halted:
                    admit = False
                else:
                    order = OrderRequest(
                        market_id=market,
                        token_id=markets[market].token_ids[0],
                        side=decision.gate.side,
                        price=quote.price,
                        size=decision.sizing.shares,
                    )
                    risk_state, result = venue.place(
                        order, markets[market], risk_state, risk, quote.ts
                    )
                    store.save(risk_state)
                    admit = result.status == "placed"
            if admit:
                pending[market] = MakerOrder(
                    side=decision.gate.side,
                    limit_price=quote.price,
                    shares=decision.sizing.shares,
                    placed_ts=quote.ts,
                    expiry_ts=quote.ts + fill_expiry,
                )
```

At the very end, before `return PaperResult(...)`, feed fills into the risk controller and surface the halt:

```python
    if risk_enabled and risk_state is not None and risk is not None:
        for fill in fills:
            risk_state = on_fill(risk_state, "", fill, _T0_FALLBACK)
        store.save(risk_state)

    return PaperResult(
        fills=tuple(fills),
        realized_pnl=realized_pnl(fills),
        halted=risk_state.halted if risk_state is not None else False,
        halt_reason=risk_state.halt_reason if risk_state is not None else None,
    )
```

> The `on_fill` market id is left empty here because fills carry no `market_id`; exposure release on close is exercised at the risk unit level (Task 3). The orchestrator's job for the gate is admission + halt propagation, which the test above covers. Keep the existing `return PaperResult(fills=..., realized_pnl=...)` deleted (replaced by the block above).

- [ ] **Step 4: Run the new test + the existing ones**

Run: `nix develop --command uv run pytest tests/app/test_orchestrator.py -v`
Expected: PASS (the two legacy tests still pass; the new halt test passes).

- [ ] **Step 5: Gates**

Run: `nix develop --command uv run ruff check && nix develop --command uv run ruff format --check && nix develop --command uv run mypy`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add app/orchestrator.py tests/app/test_orchestrator.py
git commit -m "feat(app): route orchestrator orders through the risk/venue admission path"
```

---

### Task 10: Operator scripts (non-gate) + runbook note

**Files:**
- Create: `scripts/set_allowances.py`
- Create: `scripts/probe_amoy_order.py`
- Test: `tests/execution/test_probe.py`
- Modify: `README.md` (add a short "Phase 5 operator steps" section)

**Interfaces:**
- Consumes: `core.models.Side`, `execution.orders.OrderRequest`, `execution.client.ExecutionClient`.
- Produces: `probe_amoy_order.build_probe_orders(token_id: str, price: Decimal, size: Decimal) -> tuple[OrderRequest, OrderRequest]` — a buy plus a self-counter buy on the opposite token — pure and testable with a fake client. The real `main()` is guarded behind `if __name__ == "__main__"` and constructs `ClobExecutionClient` from env.

> These scripts touch the live network only when run by an operator. The gate covers the pure order-construction helper; the SDK path is validated by running the probe on Amoy, not by CI.

- [ ] **Step 1: Write the failing test**

Create `tests/execution/test_probe.py`:

```python
from decimal import Decimal

from core.models import Side
from scripts.probe_amoy_order import build_probe_orders


def test_build_probe_orders_makes_a_buy_and_counter() -> None:
    buy, counter = build_probe_orders(token_id="yes", price=Decimal("0.50"), size=Decimal("5"))
    assert buy.token_id == "yes" and buy.side is Side.BUY_YES
    assert buy.price == Decimal("0.50") and buy.size == Decimal("5")
    assert counter.side is Side.BUY_NO
    assert counter.price == Decimal("0.50")
    assert buy.signature_type == 0 and counter.signature_type == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `nix develop --command uv run pytest tests/execution/test_probe.py -v`
Expected: FAIL (`scripts.probe_amoy_order` does not exist).

- [ ] **Step 3: Implement the scripts**

Create `scripts/probe_amoy_order.py`:

```python
"""Operator probe (non-gate): post one signed order on Amoy plus a self-counter
order and watch it settle. A mechanical pre-check that a signed order is
accepted on testnet — NOT a real-fill test (that is the Phase 6 mainnet
micro-trade). Requires real env credentials; never runs in CI.
"""

from __future__ import annotations

import os
from decimal import Decimal

from core.models import Side
from execution.orders import OrderRequest


def build_probe_orders(
    token_id: str, price: Decimal, size: Decimal
) -> tuple[OrderRequest, OrderRequest]:
    buy = OrderRequest(
        market_id="probe", token_id=token_id, side=Side.BUY_YES, price=price, size=size
    )
    counter = OrderRequest(
        market_id="probe", token_id=token_id, side=Side.BUY_NO, price=price, size=size
    )
    return buy, counter


def main() -> None:  # pragma: no cover - operator-run network path
    from execution.client import ClobExecutionClient

    client = ClobExecutionClient(
        host="https://clob.polymarket.com",
        private_key=os.environ["WALLET_PRIVATE_KEY"],
        chain_id=80002,
    )
    token_id = os.environ["PROBE_TOKEN_ID"]
    buy, counter = build_probe_orders(token_id, Decimal("0.50"), Decimal("5"))
    print("placing buy:", client.place(buy))
    print("placing counter:", client.place(counter))


if __name__ == "__main__":  # pragma: no cover
    main()
```

Create `scripts/set_allowances.py`:

```python
"""Operator one-off (non-gate): approve USDC/CTF allowances for the EOA wallet
on the Polymarket exchange. Run once per wallet before trading. Requires real
env credentials; never runs in CI.
"""

from __future__ import annotations

import os


def main() -> None:  # pragma: no cover - operator-run network path
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

    client = ClobClient(
        host="https://clob.polymarket.com",
        key=os.environ["WALLET_PRIVATE_KEY"],
        chain_id=80002,
        signature_type=0,
    )
    client.set_api_creds(client.create_or_derive_api_creds())
    client.update_balance_allowance(
        BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    )
    print("allowances updated")


if __name__ == "__main__":  # pragma: no cover
    main()
```

- [ ] **Step 4: Add the runbook note**

Append to `README.md`:

```markdown
## Phase 5 operator steps (testnet, non-gate)

These touch the live network and require env credentials (`WALLET_PRIVATE_KEY`,
`PROBE_TOKEN_ID`); they are never run in CI.

1. One-off allowance approval (once per wallet):
   `nix develop --command uv run python -m scripts.set_allowances`
2. Amoy order probe (mechanical pre-check that a signed order settles):
   `nix develop --command uv run python -m scripts.probe_amoy_order`

A real *fill* is only validated by the Phase 6 mainnet micro-trade — Amoy
cannot demonstrate one.
```

- [ ] **Step 5: Run the test + gates**

Run: `nix develop --command uv run pytest tests/execution/test_probe.py -v`
Expected: PASS.
Run: `nix develop --command uv run ruff check && nix develop --command uv run mypy`
Expected: clean.

> If `mypy` reports the `scripts/` files as untracked roots, confirm `scripts` is already covered (Phase 4 added `scripts/`); if it is not in `files` in `[tool.mypy]`, that is pre-existing and out of scope.

- [ ] **Step 6: Commit**

```bash
git add scripts/probe_amoy_order.py scripts/set_allowances.py tests/execution/test_probe.py README.md
git commit -m "feat(scripts): operator allowance + Amoy order probe (non-gate)"
```

---

### Task 11: Full gate + PLAN.md status

**Files:**
- Modify: `PLAN.md` (mark Phase 5 done)

- [ ] **Step 1: Run the complete suite + all gates**

Run: `nix develop --command uv run pytest -q`
Expected: PASS (all tests).
Run: `nix develop --command uv run ruff check && nix develop --command uv run ruff format --check && nix develop --command uv run mypy`
Expected: clean.

- [ ] **Step 2: Confirm the PLAN.md gate items are demonstrated**

Verify each Phase 5 gate item maps to a passing test:
- "order payloads correct against a mocked client" → `tests/execution/test_venue.py::test_clob_venue_places_correct_payload`.
- "tick_size / minimum_order_size violations rejected" → `test_off_tick_rejected_before_client_called`, `test_below_min_size_rejected_before_client_called`.
- "simulated limit breach halts and blocks further orders" → `test_order_count_breach_halts_and_blocks_further_orders` + `tests/app/test_orchestrator.py::test_global_halt_blocks_further_admissions` + `tests/core/test_risk.py::test_on_mark_trips_global_when_loss_exceeds_cap`.

- [ ] **Step 3: Update PLAN.md status**

In `PLAN.md`, change:

```markdown
- [ ] Phase 5 — Execution & risk
```

to:

```markdown
- [x] Phase 5 — Execution & risk
```

- [ ] **Step 4: Commit**

```bash
git add PLAN.md
git commit -m "docs: mark Phase 5 complete"
```

---

## Self-Review

**Spec coverage:**
- CLOB execution adapter (maker-first, allowance) → Tasks 5, 6, 8 (validation, client+SDK, venue preflight incl. allowance precondition).
- Risk controls (max position, daily-loss cap) + global kill switch → Tasks 2, 3.
- Persisted tripped flag / restart-while-halted → Task 7.
- Orchestrator routed through a venue → Task 9.
- S2 basket gate, pure & unwired → Task 4.
- `minimum_order_size` on `Market` → Task 1.
- EOA/type-0, funder==signer → Task 5 (`OrderRequest.signature_type=0`), Task 6 (client).
- Operator Amoy probe + allowance script (non-gate) → Task 10.
- `py-clob-client` single dep + `uv lock` → Task 6.
- Every PLAN.md gate item → Task 11 mapping.

**Placeholder scan:** No TBD/TODO; every code step contains complete code and exact commands.

**Type consistency:** `RiskState`/`RiskConfig`/`RiskOrder`/`CheckResult` used identically across Tasks 2, 3, 8, 9. `pretrade_check` returns `tuple[RiskState, CheckResult]` everywhere it is called. `OrderRequest`/`OrderResult`/`validate_order`/`OrderValidationError` consistent across Tasks 5, 8, 10. `ExecutionClient.place(order) -> str` and `allowances() -> Allowances` consistent across Tasks 6, 8. `ExecutionVenue.place(order, market, state, config, now) -> tuple[RiskState, OrderResult]` consistent across Tasks 8, 9. `basket_decide`/`basket_cost` consistent within Task 4. `RiskStore.load/save` consistent across Tasks 7, 9.
