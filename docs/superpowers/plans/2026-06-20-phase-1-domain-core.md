# Phase 1 — Domain core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pure financial core — typed models, cost model, abstain-by-default edge gate, fractional-Kelly sizing with hard caps, isotonic/Platt calibration, and metrics — composed by one pure pipeline, with every PLAN.md invariant proven by property tests.

**Architecture:** A pipeline of pure functions over frozen pydantic models. Each component is an independent module under `core/`; `core/decision.py` `evaluate(...)` wires them (calibrate → edge → cost gate → size). No network, no IO, no framework code — that is Phase 2+. `Decimal` for order-boundary money/prices (exact tick + accounting), `float` for the decision/statistical math.

**Tech Stack:** Python 3.12, pydantic v2, numpy, scikit-learn (isotonic + logistic/Platt), pytest + pytest-cov, hypothesis (property tests), ruff, mypy (strict). Toolchain runs inside `nix develop` via `uv`.

**Spec:** `docs/superpowers/specs/2026-06-20-phase-1-domain-core-design.md`

## Global Constraints

These apply to every task:

- **TDD, red→green.** Write the failing test first; no task is done until its tests pass **and** `uv run ruff check`, `uv run ruff format --check`, `uv run mypy` are clean. Never advance with a red gate.
- **Pure core, no network.** No `httpx`/`websockets`/sklearn-fetching-anything; no file/socket IO. Functions are pure over validated inputs.
- **Numeric split:** `Decimal` for prices, share quantities, cash, P&L; `float` for edge, cost hurdle, Kelly fraction, calibration, Brier. Convert explicitly at the boundary via `Decimal(str(x))`.
- **Cost gate is law:** the abstain test (`abs(edge) < hurdle ⇒ ABSTAIN`) lives only in `core/edge_gate.py`.
- **Prices** are `Decimal` in `(0, 1)`, on `tick_size` (default `0.01`); `0` and `1` are rejected.
- **New runtime deps:** `numpy`, `scikit-learn` only (scipy is transitive via sklearn — do not declare it). After any dependency change run `uv lock` before commit or CI `--frozen` fails.
- **Commands run in the devshell:** prefix with `nix develop --command` (e.g. `nix develop --command uv run pytest`).
- **Attribution:** commit messages carry NO `Co-Authored-By` / "Generated with" trailers (per `AGENTS.md`).

## Git & commit protocol (read before Task 1)

- Work currently sits on branch `phase-0-foundation`. Per `CLAUDE.md` ("one plan step per worktree/branch") and `AGENTS.md`, Phase 1 gets its own branch. **Before Task 1, confirm with the user:** branch `phase-1-domain-core` in place vs. a worktree from `main`. All Phase 1 commits land on that branch.
- **Commit only on explicit user instruction.** Commit steps are written out, but the executor runs them only once authorized.
- Stage files **explicitly by path** — never `git add -A` / `git add .`.
- Do not push unless asked.

---

### Task 1: Add numpy + scikit-learn deps

**Files:**
- Modify: `pyproject.toml` (add to `[project] dependencies`)
- Modify (generated): `uv.lock`

**Interfaces:**
- Produces: `numpy` and `sklearn` importable in the project venv. All later tasks rely on this.

- [ ] **Step 1: Add the dependencies to `pyproject.toml`**

Change the `dependencies` array under `[project]` from:
```toml
dependencies = [
    "pydantic>=2",
]
```
to:
```toml
dependencies = [
    "pydantic>=2",
    "numpy>=2",
    "scikit-learn>=1.5",
]
```

- [ ] **Step 2: Lock and sync**

Run:
```bash
nix develop --command uv lock
nix develop --command uv sync
```
Expected: `uv.lock` updated with `numpy`, `scikit-learn`, `scipy` (transitive), `joblib`, `threadpoolctl`; `.venv` populated.

- [ ] **Step 3: Verify imports resolve**

Run: `nix develop --command uv run python -c "import numpy, sklearn; print(numpy.__version__, sklearn.__version__)"`
Expected: two version strings, no `ModuleNotFoundError`.

- [ ] **Step 4: Confirm gates still green on the existing suite**

Run: `nix develop --command uv run pytest`
Expected: the Phase 0 smoke test still `passed`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add numpy + scikit-learn for the domain core"
```

---

### Task 2: Domain models (`core/models.py`)

**Files:**
- Create: `core/models.py`
- Create: `tests/core/__init__.py` (empty)
- Create: `tests/core/test_models.py`

**Interfaces:**
- Produces (consumed by every later task):
  - `class Side(str, Enum)`: `BUY_YES = "buy_yes"`, `BUY_NO = "buy_no"`.
  - `class CostInputs`: fields `spread, fee_rate, gas_usd, model_error_margin: Decimal` (all `>= 0`).
  - `class TradeCandidate`: `price: Decimal`, `raw_prob: float`, `costs: CostInputs`, `notional_hint: Decimal`, `tick_size: Decimal = Decimal("0.01")`.
  - `class RiskLimits`: `bankroll: Decimal`, `kelly_fraction: float`, `max_position_fraction: float`, `max_position_usd: Decimal`.
  - `class GateResult`: `action: Literal["act","abstain"]`, `side: Side | None`, `edge: float | None`, `reason: str | None`; classmethods `act(side, edge)` and `abstain(reason)`.
  - `class SizingResult`: `stake_usd: Decimal`, `shares: Decimal`, `binding_cap: str | None`.
  - `class Decision`: `gate: GateResult`, `sizing: SizingResult`.
  - `class Fill`: `side: Side`, `entry_price: Decimal`, `exit_price: Decimal`, `shares: Decimal`, `costs_usd: Decimal` (entry/exit are the traded token's own prices).
  - `class CalibrationSample`: `raw_prob: float`, `outcome: int` (0/1).

- [ ] **Step 1: Write the failing tests**

`tests/core/__init__.py`: empty file.

`tests/core/test_models.py`:
```python
"""Domain model validation: tick alignment, bounds, tagged-union helpers."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.models import (
    CostInputs,
    GateResult,
    RiskLimits,
    Side,
    TradeCandidate,
)


def _costs() -> CostInputs:
    return CostInputs(
        spread=Decimal("0.01"),
        fee_rate=Decimal("0"),
        gas_usd=Decimal("0.05"),
        model_error_margin=Decimal("0.01"),
    )


def test_candidate_accepts_on_tick_price() -> None:
    c = TradeCandidate(
        price=Decimal("0.55"),
        raw_prob=0.6,
        costs=_costs(),
        notional_hint=Decimal("10"),
    )
    assert c.price == Decimal("0.55")
    assert c.tick_size == Decimal("0.01")


def test_candidate_rejects_off_tick_price() -> None:
    with pytest.raises(ValidationError):
        TradeCandidate(
            price=Decimal("0.555"),
            raw_prob=0.6,
            costs=_costs(),
            notional_hint=Decimal("10"),
        )


@pytest.mark.parametrize("bad", [Decimal("0"), Decimal("1"), Decimal("1.5")])
def test_candidate_rejects_out_of_range_price(bad: Decimal) -> None:
    with pytest.raises(ValidationError):
        TradeCandidate(
            price=bad,
            raw_prob=0.6,
            costs=_costs(),
            notional_hint=Decimal("10"),
        )


def test_costs_reject_negative() -> None:
    with pytest.raises(ValidationError):
        CostInputs(
            spread=Decimal("-0.01"),
            fee_rate=Decimal("0"),
            gas_usd=Decimal("0"),
            model_error_margin=Decimal("0"),
        )


def test_risk_limits_reject_kelly_above_one() -> None:
    with pytest.raises(ValidationError):
        RiskLimits(
            bankroll=Decimal("25"),
            kelly_fraction=1.5,
            max_position_fraction=0.2,
            max_position_usd=Decimal("5"),
        )


def test_gate_result_constructors() -> None:
    act = GateResult.act(side=Side.BUY_YES, edge=0.05)
    assert act.action == "act"
    assert act.side is Side.BUY_YES
    assert act.edge == 0.05

    out = GateResult.abstain(reason="below hurdle")
    assert out.action == "abstain"
    assert out.side is None
    assert out.reason == "below hurdle"


def test_models_are_frozen() -> None:
    c = _costs()
    with pytest.raises(ValidationError):
        c.spread = Decimal("0.02")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix develop --command uv run pytest tests/core/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.models'`.

- [ ] **Step 3: Implement `core/models.py`**

```python
"""Typed domain models for the trading core.

Decimal for money/prices (order-boundary: exact tick alignment and accounting);
float for the statistical/decision math handled elsewhere.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_TICK = Decimal("0.01")


class Side(str, Enum):
    BUY_YES = "buy_yes"
    BUY_NO = "buy_no"


class CostInputs(BaseModel):
    model_config = ConfigDict(frozen=True)

    spread: Decimal = Field(ge=0)
    fee_rate: Decimal = Field(ge=0)
    gas_usd: Decimal = Field(ge=0)
    model_error_margin: Decimal = Field(ge=0)


class TradeCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    price: Decimal = Field(gt=0, lt=1)
    raw_prob: float = Field(ge=0.0, le=1.0)
    costs: CostInputs
    notional_hint: Decimal = Field(gt=0)
    tick_size: Decimal = Field(default=DEFAULT_TICK, gt=0)

    @model_validator(mode="after")
    def _price_on_tick(self) -> Self:
        if self.price % self.tick_size != 0:
            raise ValueError(
                f"price {self.price} is not a multiple of tick {self.tick_size}"
            )
        return self


class RiskLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    bankroll: Decimal = Field(gt=0)
    kelly_fraction: float = Field(gt=0.0, le=1.0)
    max_position_fraction: float = Field(gt=0.0, le=1.0)
    max_position_usd: Decimal = Field(gt=0)


class GateResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: Literal["act", "abstain"]
    side: Side | None = None
    edge: float | None = None
    reason: str | None = None

    @classmethod
    def act(cls, side: Side, edge: float) -> GateResult:
        return cls(action="act", side=side, edge=edge)

    @classmethod
    def abstain(cls, reason: str) -> GateResult:
        return cls(action="abstain", reason=reason)


class SizingResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    stake_usd: Decimal = Field(ge=0)
    shares: Decimal = Field(ge=0)
    binding_cap: str | None = None


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True)

    gate: GateResult
    sizing: SizingResult


class Fill(BaseModel):
    model_config = ConfigDict(frozen=True)

    side: Side
    entry_price: Decimal = Field(gt=0, lt=1)
    exit_price: Decimal = Field(gt=0, lt=1)
    shares: Decimal = Field(ge=0)
    costs_usd: Decimal = Field(ge=0)


class CalibrationSample(BaseModel):
    model_config = ConfigDict(frozen=True)

    raw_prob: float = Field(ge=0.0, le=1.0)
    outcome: int = Field(ge=0, le=1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix develop --command uv run pytest tests/core/test_models.py -v`
Expected: all tests `PASSED`.

- [ ] **Step 5: Run the gates**

Run: `nix develop --command bash -c 'uv run ruff check && uv run ruff format --check && uv run mypy'`
Expected: ruff clean, format clean, mypy `Success`.

- [ ] **Step 6: Commit**

```bash
git add core/models.py tests/core/__init__.py tests/core/test_models.py
git commit -m "feat(core): typed domain models with tick + bounds validation"
```

---

### Task 3: Cost model (`core/cost_model.py`)

**Files:**
- Create: `core/cost_model.py`
- Create: `tests/core/test_cost_model.py`

**Interfaces:**
- Consumes: `CostInputs` (Task 2).
- Produces: `round_trip_cost(costs: CostInputs, notional: Decimal) -> float` — the per-share price hurdle: `spread + 2*fee_rate + gas_usd/notional + model_error_margin`. Non-negative, non-decreasing in each input. Raises `ValueError` if `notional <= 0`.

- [ ] **Step 1: Write the failing tests**

`tests/core/test_cost_model.py`:
```python
"""Cost model: composition, non-negativity, monotonicity."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from core.cost_model import round_trip_cost
from core.models import CostInputs


def test_round_trip_cost_components() -> None:
    costs = CostInputs(
        spread=Decimal("0.02"),
        fee_rate=Decimal("0.01"),
        gas_usd=Decimal("1.00"),
        model_error_margin=Decimal("0.005"),
    )
    # 0.02 + 2*0.01 + 1.00/10 + 0.005 = 0.145
    assert round_trip_cost(costs, Decimal("10")) == pytest.approx(0.145)


def test_round_trip_cost_rejects_nonpositive_notional() -> None:
    costs = CostInputs(
        spread=Decimal("0.01"),
        fee_rate=Decimal("0"),
        gas_usd=Decimal("0"),
        model_error_margin=Decimal("0"),
    )
    with pytest.raises(ValueError):
        round_trip_cost(costs, Decimal("0"))


_money = st.decimals(
    min_value=Decimal("0"), max_value=Decimal("5"), places=4, allow_nan=False
)
_pos_notional = st.decimals(
    min_value=Decimal("0.01"), max_value=Decimal("1000"), places=2, allow_nan=False
)


@given(_money, _money, _money, _money, _pos_notional)
def test_round_trip_cost_non_negative(
    spread: Decimal,
    fee_rate: Decimal,
    gas: Decimal,
    margin: Decimal,
    notional: Decimal,
) -> None:
    costs = CostInputs(
        spread=spread, fee_rate=fee_rate, gas_usd=gas, model_error_margin=margin
    )
    assert round_trip_cost(costs, notional) >= 0.0


@given(_money, _money, _money, _money, _money, _pos_notional)
def test_round_trip_cost_monotone_in_spread(
    spread: Decimal,
    bump: Decimal,
    fee_rate: Decimal,
    gas: Decimal,
    margin: Decimal,
    notional: Decimal,
) -> None:
    base = CostInputs(
        spread=spread, fee_rate=fee_rate, gas_usd=gas, model_error_margin=margin
    )
    higher = CostInputs(
        spread=spread + bump,
        fee_rate=fee_rate,
        gas_usd=gas,
        model_error_margin=margin,
    )
    assert round_trip_cost(higher, notional) >= round_trip_cost(base, notional)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix develop --command uv run pytest tests/core/test_cost_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.cost_model'`.

- [ ] **Step 3: Implement `core/cost_model.py`**

```python
"""Round-trip cost model: the per-share price hurdle an edge must clear."""

from __future__ import annotations

from decimal import Decimal

from core.models import CostInputs


def round_trip_cost(costs: CostInputs, notional: Decimal) -> float:
    """Return the per-share price hurdle in price units.

    spread (both legs) + round-trip fees (2 * fee_rate) + amortized round-trip
    gas (gas_usd / notional) + model error margin.
    """
    if notional <= 0:
        raise ValueError("notional must be positive")
    spread = float(costs.spread)
    fees = 2.0 * float(costs.fee_rate)
    gas = float(costs.gas_usd) / float(notional)
    margin = float(costs.model_error_margin)
    return spread + fees + gas + margin
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix develop --command uv run pytest tests/core/test_cost_model.py -v`
Expected: all `PASSED` (property tests run many examples).

- [ ] **Step 5: Run the gates**

Run: `nix develop --command bash -c 'uv run ruff check && uv run ruff format --check && uv run mypy'`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add core/cost_model.py tests/core/test_cost_model.py
git commit -m "feat(core): round-trip cost model with monotonicity tests"
```

---

### Task 4: Edge gate (`core/edge_gate.py`)

**Files:**
- Create: `core/edge_gate.py`
- Create: `tests/core/test_edge_gate.py`

**Interfaces:**
- Consumes: `TradeCandidate`, `GateResult`, `Side` (Task 2).
- Produces: `decide(candidate: TradeCandidate, q: float, hurdle: float) -> GateResult`. `edge = q - float(candidate.price)`; ACT iff `abs(edge) >= hurdle` (side `BUY_YES` if `edge > 0` else `BUY_NO`); else ABSTAIN. **The cost-gate law lives only here.**

- [ ] **Step 1: Write the failing tests**

`tests/core/test_edge_gate.py`:
```python
"""Edge gate: abstain-by-default and the cost-gate law (property)."""

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from core.edge_gate import decide
from core.models import CostInputs, Side, TradeCandidate


def _candidate(price: str) -> TradeCandidate:
    return TradeCandidate(
        price=Decimal(price),
        raw_prob=0.5,
        costs=CostInputs(
            spread=Decimal("0.01"),
            fee_rate=Decimal("0"),
            gas_usd=Decimal("0"),
            model_error_margin=Decimal("0"),
        ),
        notional_hint=Decimal("10"),
    )


def test_acts_long_when_edge_clears_hurdle() -> None:
    result = decide(_candidate("0.50"), q=0.60, hurdle=0.05)
    assert result.action == "act"
    assert result.side is Side.BUY_YES
    assert result.edge is not None and result.edge > 0


def test_acts_short_when_negative_edge_clears_hurdle() -> None:
    result = decide(_candidate("0.50"), q=0.40, hurdle=0.05)
    assert result.action == "act"
    assert result.side is Side.BUY_NO


def test_abstains_when_edge_below_hurdle() -> None:
    result = decide(_candidate("0.50"), q=0.52, hurdle=0.05)
    assert result.action == "abstain"


_price = st.integers(min_value=1, max_value=99).map(lambda n: Decimal(n) / 100)
_q = st.floats(min_value=0.0, max_value=1.0)
_hurdle = st.floats(min_value=0.0, max_value=1.0)


@given(_price, _q, _hurdle)
def test_cost_gate_is_law(price: Decimal, q: float, hurdle: float) -> None:
    candidate = TradeCandidate(
        price=price,
        raw_prob=0.5,
        costs=CostInputs(
            spread=Decimal("0"),
            fee_rate=Decimal("0"),
            gas_usd=Decimal("0"),
            model_error_margin=Decimal("0"),
        ),
        notional_hint=Decimal("10"),
    )
    result = decide(candidate, q, hurdle)
    edge = q - float(price)
    if abs(edge) < hurdle:
        assert result.action == "abstain"
    else:
        assert result.action == "act"
        assert result.edge is not None and abs(result.edge) >= hurdle
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix develop --command uv run pytest tests/core/test_edge_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.edge_gate'`.

- [ ] **Step 3: Implement `core/edge_gate.py`**

```python
"""Edge gate: abstain-by-default. Acts only when the edge clears the hurdle."""

from __future__ import annotations

from core.models import GateResult, Side, TradeCandidate


def decide(candidate: TradeCandidate, q: float, hurdle: float) -> GateResult:
    """Decide whether to act on a candidate given calibrated prob q and hurdle.

    The cost-gate law: never act when abs(edge) < hurdle.
    """
    edge = q - float(candidate.price)
    if abs(edge) < hurdle:
        return GateResult.abstain(
            reason=f"edge {edge:.4f} below hurdle {hurdle:.4f}"
        )
    side = Side.BUY_YES if edge > 0 else Side.BUY_NO
    return GateResult.act(side=side, edge=edge)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix develop --command uv run pytest tests/core/test_edge_gate.py -v`
Expected: all `PASSED`.

- [ ] **Step 5: Run the gates**

Run: `nix develop --command bash -c 'uv run ruff check && uv run ruff format --check && uv run mypy'`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add core/edge_gate.py tests/core/test_edge_gate.py
git commit -m "feat(core): abstain-by-default edge gate with cost-gate law property"
```

---

### Task 5: Sizing (`core/sizing.py`)

**Files:**
- Create: `core/sizing.py`
- Create: `tests/core/test_sizing.py`

**Interfaces:**
- Consumes: `TradeCandidate`, `GateResult`, `RiskLimits`, `SizingResult`, `Side` (Task 2).
- Produces:
  - `kelly_fraction(q: float, p: float, side: Side) -> float` — `(q-p)/(1-p)` for `BUY_YES`, `(p-q)/p` for `BUY_NO`, clamped to `[0, 1]`.
  - `size(candidate: TradeCandidate, gate: GateResult, limits: RiskLimits) -> SizingResult` — fractional Kelly then clamped by `max_position_fraction*bankroll`, `max_position_usd`, `bankroll`; records `binding_cap`. ABSTAIN ⇒ zero. `shares = stake / entry_price` where `entry_price = p` (YES) or `1-p` (NO).

- [ ] **Step 1: Write the failing tests**

`tests/core/test_sizing.py`:
```python
"""Sizing: fractional Kelly, hard caps, abstain-is-zero (property)."""

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from core.edge_gate import decide
from core.models import CostInputs, GateResult, RiskLimits, Side, TradeCandidate
from core.sizing import kelly_fraction, size


def _candidate(price: str) -> TradeCandidate:
    return TradeCandidate(
        price=Decimal(price),
        raw_prob=0.5,
        costs=CostInputs(
            spread=Decimal("0"),
            fee_rate=Decimal("0"),
            gas_usd=Decimal("0"),
            model_error_margin=Decimal("0"),
        ),
        notional_hint=Decimal("10"),
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        bankroll=Decimal("25"),
        kelly_fraction=0.25,
        max_position_fraction=0.2,  # cap = 5
        max_position_usd=Decimal("4"),  # strictly smallest cap
    )


def test_kelly_fraction_yes_and_no() -> None:
    # YES: (0.6-0.5)/(1-0.5) = 0.2
    assert kelly_fraction(0.6, 0.5, Side.BUY_YES) == 0.2
    # NO: (0.5-0.4)/0.5 = 0.2
    assert kelly_fraction(0.4, 0.5, Side.BUY_NO) == 0.2


def test_kelly_fraction_clamps_negative_to_zero() -> None:
    assert kelly_fraction(0.4, 0.5, Side.BUY_YES) == 0.0


def test_abstain_sizes_to_zero() -> None:
    gate = GateResult.abstain(reason="x")
    result = size(_candidate("0.50"), gate, _limits())
    assert result.stake_usd == Decimal("0")
    assert result.shares == Decimal("0")


def test_cap_binds_and_is_recorded() -> None:
    # Big edge → uncapped Kelly stake 5.625; max_position_usd=4 is the smallest cap.
    gate = decide(_candidate("0.50"), q=0.95, hurdle=0.01)
    result = size(_candidate("0.50"), gate, _limits())
    assert result.stake_usd == Decimal("4")
    assert result.binding_cap == "max_position_usd"


_price = st.integers(min_value=1, max_value=99).map(lambda n: Decimal(n) / 100)
_q = st.floats(min_value=0.0, max_value=1.0)


@given(_price, _q)
def test_stake_never_exceeds_caps_or_bankroll(price: Decimal, q: float) -> None:
    candidate = _candidate(str(price))
    limits = _limits()
    gate = decide(candidate, q, hurdle=0.0)
    result = size(candidate, gate, limits)
    cap = min(
        Decimal(str(limits.max_position_fraction)) * limits.bankroll,
        limits.max_position_usd,
        limits.bankroll,
    )
    assert result.stake_usd >= Decimal("0")
    assert result.stake_usd <= cap
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix develop --command uv run pytest tests/core/test_sizing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.sizing'`.

- [ ] **Step 3: Implement `core/sizing.py`**

```python
"""Position sizing: fractional Kelly with hard caps. Abstain ⇒ zero."""

from __future__ import annotations

from decimal import Decimal

from core.models import GateResult, RiskLimits, Side, SizingResult, TradeCandidate


def kelly_fraction(q: float, p: float, side: Side) -> float:
    """Full-Kelly fraction of bankroll for the bought token, clamped to [0, 1]."""
    if side is Side.BUY_YES:
        f = (q - p) / (1.0 - p)
    else:
        f = (p - q) / p
    return max(0.0, min(1.0, f))


def size(
    candidate: TradeCandidate, gate: GateResult, limits: RiskLimits
) -> SizingResult:
    """Size a position from a gate decision under fractional Kelly + hard caps."""
    if gate.action == "abstain":
        return SizingResult(stake_usd=Decimal(0), shares=Decimal(0), binding_cap=None)

    assert gate.side is not None and gate.edge is not None
    p = float(candidate.price)
    q = gate.edge + p  # edge == q - p regardless of side
    f_star = kelly_fraction(q, p, gate.side)

    stake = Decimal(str(f_star * limits.kelly_fraction)) * limits.bankroll
    binding_cap: str | None = None
    caps = (
        (
            "max_position_fraction",
            Decimal(str(limits.max_position_fraction)) * limits.bankroll,
        ),
        ("max_position_usd", limits.max_position_usd),
        ("bankroll", limits.bankroll),
    )
    for name, cap in caps:
        if stake > cap:
            stake = cap
            binding_cap = name

    entry_price = (
        candidate.price if gate.side is Side.BUY_YES else Decimal(1) - candidate.price
    )
    shares = stake / entry_price
    return SizingResult(stake_usd=stake, shares=shares, binding_cap=binding_cap)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix develop --command uv run pytest tests/core/test_sizing.py -v`
Expected: all `PASSED`.

- [ ] **Step 5: Run the gates**

Run: `nix develop --command bash -c 'uv run ruff check && uv run ruff format --check && uv run mypy'`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add core/sizing.py tests/core/test_sizing.py
git commit -m "feat(core): fractional-Kelly sizing with hard caps"
```

---

### Task 6: Calibration (`core/calibration.py`)

**Files:**
- Create: `core/calibration.py`
- Create: `tests/core/test_calibration.py`
- Modify: `pyproject.toml` (add mypy override for sklearn)

**Interfaces:**
- Consumes: `CalibrationSample` (Task 2).
- Produces:
  - `class Calibrator(Protocol)`: `fit(samples: Sequence[CalibrationSample]) -> None`, `predict(raw: float) -> float`.
  - `class IsotonicCalibrator` and `class PlattCalibrator` implementing it. `predict` on an unfitted calibrator raises `RuntimeError`; output clamped to `[0, 1]`; monotonic non-decreasing in `raw`.

- [ ] **Step 1: Add the sklearn mypy override to `pyproject.toml`**

Append (sklearn ships no complete type stubs; numpy is typed and needs no override):
```toml
[[tool.mypy.overrides]]
module = ["sklearn.*"]
ignore_missing_imports = true
```

- [ ] **Step 2: Write the failing tests**

`tests/core/test_calibration.py`:
```python
"""Calibration: fitted-guard, range, monotonicity, Brier reduction."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from core.calibration import IsotonicCalibrator, PlattCalibrator
from core.models import CalibrationSample


def _brier(probs: list[float], outcomes: list[int]) -> float:
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes, strict=True)) / len(probs)


def _monotone_samples() -> list[CalibrationSample]:
    # Outcome probability rises with raw_prob: a clean calibration dataset.
    samples: list[CalibrationSample] = []
    for i in range(200):
        raw = i / 199
        outcome = 1 if (i % 100) < int(raw * 100) else 0
        samples.append(CalibrationSample(raw_prob=raw, outcome=outcome))
    return samples


@pytest.mark.parametrize("cls", [IsotonicCalibrator, PlattCalibrator])
def test_predict_before_fit_raises(cls: type) -> None:
    with pytest.raises(RuntimeError):
        cls().predict(0.5)


@pytest.mark.parametrize("cls", [IsotonicCalibrator, PlattCalibrator])
def test_predict_in_unit_range(cls: type) -> None:
    cal = cls()
    cal.fit(_monotone_samples())
    for raw in (0.0, 0.25, 0.5, 0.75, 1.0):
        out = cal.predict(raw)
        assert 0.0 <= out <= 1.0


@pytest.mark.parametrize("cls", [IsotonicCalibrator, PlattCalibrator])
@given(
    a=st.floats(min_value=0.0, max_value=1.0),
    b=st.floats(min_value=0.0, max_value=1.0),
)
def test_monotonic(cls: type, a: float, b: float) -> None:
    cal = cls()
    cal.fit(_monotone_samples())
    lo, hi = sorted((a, b))
    assert cal.predict(lo) <= cal.predict(hi) + 1e-9


def test_isotonic_reduces_brier_on_overconfident_inputs() -> None:
    # Construct overconfident raw probs: true base rate 0.5, raw pushed to 0/1.
    samples: list[CalibrationSample] = []
    raws: list[float] = []
    outcomes: list[int] = []
    for i in range(400):
        outcome = i % 2  # exactly 50% base rate
        raw = 0.95 if outcome == 1 else 0.05  # overconfident, but correct direction
        # Flip a known fraction so raw is not perfectly separating.
        if i % 5 == 0:
            outcome = 1 - outcome
        samples.append(CalibrationSample(raw_prob=raw, outcome=outcome))
        raws.append(raw)
        outcomes.append(outcome)

    cal = IsotonicCalibrator()
    cal.fit(samples)
    calibrated = [cal.predict(r) for r in raws]

    assert _brier(calibrated, outcomes) <= _brier(raws, outcomes)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `nix develop --command uv run pytest tests/core/test_calibration.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.calibration'`.

- [ ] **Step 4: Implement `core/calibration.py`**

```python
"""Probability calibration: isotonic and Platt (logistic), behind one Protocol."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from core.models import CalibrationSample


class Calibrator(Protocol):
    def fit(self, samples: Sequence[CalibrationSample]) -> None: ...
    def predict(self, raw: float) -> float: ...


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


class IsotonicCalibrator:
    def __init__(self) -> None:
        self._model: IsotonicRegression | None = None

    def fit(self, samples: Sequence[CalibrationSample]) -> None:
        x = np.array([s.raw_prob for s in samples], dtype=float)
        y = np.array([s.outcome for s in samples], dtype=float)
        model = IsotonicRegression(
            y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip"
        )
        model.fit(x, y)
        self._model = model

    def predict(self, raw: float) -> float:
        if self._model is None:
            raise RuntimeError("calibrator is not fitted")
        return _clip(float(self._model.predict([raw])[0]))


class PlattCalibrator:
    def __init__(self) -> None:
        self._model: LogisticRegression | None = None

    def fit(self, samples: Sequence[CalibrationSample]) -> None:
        x = np.array([[s.raw_prob] for s in samples], dtype=float)
        y = np.array([s.outcome for s in samples], dtype=int)
        model = LogisticRegression()
        model.fit(x, y)
        self._model = model

    def predict(self, raw: float) -> float:
        if self._model is None:
            raise RuntimeError("calibrator is not fitted")
        return _clip(float(self._model.predict_proba([[raw]])[0, 1]))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `nix develop --command uv run pytest tests/core/test_calibration.py -v`
Expected: all `PASSED`.

- [ ] **Step 6: Run the gates**

Run: `nix develop --command bash -c 'uv run ruff check && uv run ruff format --check && uv run mypy'`
Expected: clean (the mypy override silences sklearn's missing stubs).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml core/calibration.py tests/core/test_calibration.py
git commit -m "feat(core): isotonic + Platt calibration with monotonicity + Brier tests"
```

---

### Task 7: Metrics (`core/metrics.py`)

**Files:**
- Create: `core/metrics.py`
- Create: `tests/core/test_metrics.py`

**Interfaces:**
- Consumes: `Fill` (Task 2).
- Produces:
  - `brier_score(probs: Sequence[float], outcomes: Sequence[int]) -> float`.
  - `calibration_curve(probs, outcomes, bins: int) -> list[tuple[float, float, int]]` — per non-empty bin `(mean_pred, mean_obs, count)`.
  - `realized_pnl(fills: Sequence[Fill]) -> Decimal`.
  - `roi(pnl: Decimal, deployed: Decimal) -> float`.

- [ ] **Step 1: Write the failing tests**

`tests/core/test_metrics.py`:
```python
"""Metrics: Brier, calibration curve, P&L accounting balances (property)."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from core.metrics import brier_score, calibration_curve, realized_pnl, roi
from core.models import Fill, Side


def test_brier_score_perfect_is_zero() -> None:
    assert brier_score([1.0, 0.0], [1, 0]) == 0.0


def test_brier_score_known_value() -> None:
    # ((0.5-1)^2 + (0.5-0)^2)/2 = 0.25
    assert brier_score([0.5, 0.5], [1, 0]) == pytest.approx(0.25)


def test_calibration_curve_bins() -> None:
    curve = calibration_curve([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1], bins=2)
    assert len(curve) == 2
    lo_pred, lo_obs, lo_n = curve[0]
    assert lo_obs == 0.0 and lo_n == 2
    hi_pred, hi_obs, hi_n = curve[1]
    assert hi_obs == 1.0 and hi_n == 2


def test_roi() -> None:
    assert roi(Decimal("5"), Decimal("25")) == pytest.approx(0.2)


def test_round_trip_at_same_price_loses_only_costs() -> None:
    fill = Fill(
        side=Side.BUY_YES,
        entry_price=Decimal("0.50"),
        exit_price=Decimal("0.50"),
        shares=Decimal("10"),
        costs_usd=Decimal("0.30"),
    )
    assert realized_pnl([fill]) == Decimal("-0.30")


_price = st.integers(min_value=1, max_value=99).map(lambda n: Decimal(n) / 100)
_shares = st.integers(min_value=0, max_value=100).map(Decimal)
_costs = st.integers(min_value=0, max_value=500).map(lambda c: Decimal(c) / 100)


@given(
    entry=_price,
    exit_=_price,
    shares=_shares,
    costs=_costs,
    bankroll_start=st.integers(min_value=1, max_value=1000).map(Decimal),
)
def test_pnl_accounting_balances(
    entry: Decimal,
    exit_: Decimal,
    shares: Decimal,
    costs: Decimal,
    bankroll_start: Decimal,
) -> None:
    fill = Fill(
        side=Side.BUY_YES,
        entry_price=entry,
        exit_price=exit_,
        shares=shares,
        costs_usd=costs,
    )
    pnl = realized_pnl([fill])
    # Cashflow: pay cost basis + fees on entry, receive proceeds on exit.
    bankroll_end = bankroll_start - entry * shares - costs + exit_ * shares
    assert bankroll_end == bankroll_start + pnl  # exact Decimal equality


@given(st.lists(st.tuples(_price, _price, _shares, _costs), max_size=5))
def test_realized_pnl_is_additive(
    rows: list[tuple[Decimal, Decimal, Decimal, Decimal]],
) -> None:
    fills = [
        Fill(
            side=Side.BUY_YES,
            entry_price=e,
            exit_price=x,
            shares=s,
            costs_usd=c,
        )
        for e, x, s, c in rows
    ]
    total = realized_pnl(fills)
    piecewise = sum((realized_pnl([f]) for f in fills), Decimal(0))
    assert total == piecewise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix develop --command uv run pytest tests/core/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.metrics'`.

- [ ] **Step 3: Implement `core/metrics.py`**

```python
"""Scoring and P&L metrics. Float for scores; Decimal for cash (exact)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from core.models import Fill


def brier_score(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    if len(probs) != len(outcomes):
        raise ValueError("probs and outcomes length mismatch")
    if not probs:
        raise ValueError("empty input")
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes, strict=True)) / len(probs)


def calibration_curve(
    probs: Sequence[float], outcomes: Sequence[int], bins: int
) -> list[tuple[float, float, int]]:
    if bins <= 0:
        raise ValueError("bins must be positive")
    if len(probs) != len(outcomes):
        raise ValueError("probs and outcomes length mismatch")
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for p, o in zip(probs, outcomes, strict=True):
        idx = min(bins - 1, int(p * bins))
        buckets[idx].append((p, o))
    curve: list[tuple[float, float, int]] = []
    for bucket in buckets:
        if not bucket:
            continue
        n = len(bucket)
        mean_pred = sum(p for p, _ in bucket) / n
        mean_obs = sum(o for _, o in bucket) / n
        curve.append((mean_pred, mean_obs, n))
    return curve


def realized_pnl(fills: Sequence[Fill]) -> Decimal:
    total = Decimal(0)
    for f in fills:
        total += (f.exit_price - f.entry_price) * f.shares - f.costs_usd
    return total


def roi(pnl: Decimal, deployed: Decimal) -> float:
    if deployed <= 0:
        raise ValueError("deployed must be positive")
    return float(pnl / deployed)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix develop --command uv run pytest tests/core/test_metrics.py -v`
Expected: all `PASSED`.

- [ ] **Step 5: Run the gates**

Run: `nix develop --command bash -c 'uv run ruff check && uv run ruff format --check && uv run mypy'`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add core/metrics.py tests/core/test_metrics.py
git commit -m "feat(core): Brier, calibration curve, P&L accounting metrics"
```

---

### Task 8: Decision pipeline (`core/decision.py`)

**Files:**
- Create: `core/decision.py`
- Create: `tests/core/test_decision.py`

**Interfaces:**
- Consumes: `round_trip_cost` (Task 3), `decide` (Task 4), `size` (Task 5), `Calibrator` (Task 6), `TradeCandidate`/`RiskLimits`/`Decision` (Task 2).
- Produces: `evaluate(candidate: TradeCandidate, calibrator: Calibrator, limits: RiskLimits) -> Decision` — the pure pipeline calibrate → cost → gate → size.

- [ ] **Step 1: Write the failing tests**

`tests/core/test_decision.py`:
```python
"""Pure decision pipeline: compose calibrate → cost → gate → size."""

from collections.abc import Sequence
from decimal import Decimal

from core.decision import evaluate
from core.models import CalibrationSample, CostInputs, RiskLimits, Side, TradeCandidate


class _FixedCalibrator:
    """Test double: returns a preset calibrated probability."""

    def __init__(self, value: float) -> None:
        self._value = value

    def fit(self, samples: Sequence[CalibrationSample]) -> None:
        return None

    def predict(self, raw: float) -> float:
        return self._value


def _candidate(spread: str) -> TradeCandidate:
    return TradeCandidate(
        price=Decimal("0.50"),
        raw_prob=0.5,
        costs=CostInputs(
            spread=Decimal(spread),
            fee_rate=Decimal("0"),
            gas_usd=Decimal("0"),
            model_error_margin=Decimal("0"),
        ),
        notional_hint=Decimal("10"),
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        bankroll=Decimal("25"),
        kelly_fraction=0.25,
        max_position_fraction=0.2,
        max_position_usd=Decimal("5"),
    )


def test_abstains_below_hurdle_with_zero_size() -> None:
    # q=0.52, price=0.50 → edge 0.02; hurdle = spread 0.05 → abstain.
    decision = evaluate(_candidate("0.05"), _FixedCalibrator(0.52), _limits())
    assert decision.gate.action == "abstain"
    assert decision.sizing.stake_usd == Decimal("0")
    assert decision.sizing.shares == Decimal("0")


def test_acts_above_hurdle_with_capped_nonzero_size() -> None:
    # q=0.70, price=0.50 → edge 0.20; hurdle = spread 0.01 → act long, capped.
    decision = evaluate(_candidate("0.01"), _FixedCalibrator(0.70), _limits())
    assert decision.gate.action == "act"
    assert decision.gate.side is Side.BUY_YES
    assert decision.sizing.stake_usd > Decimal("0")
    assert decision.sizing.stake_usd <= Decimal("5")  # max_position_usd cap
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix develop --command uv run pytest tests/core/test_decision.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.decision'`.

- [ ] **Step 3: Implement `core/decision.py`**

```python
"""Pure decision pipeline: calibrate → cost → gate → size.

This is the in-core composition of the pure functions. It is NOT Phase 4
assembly (which wires live signals, IO, and asyncio). No network here.
"""

from __future__ import annotations

from core.calibration import Calibrator
from core.cost_model import round_trip_cost
from core.edge_gate import decide
from core.models import Decision, RiskLimits, TradeCandidate
from core.sizing import size


def evaluate(
    candidate: TradeCandidate, calibrator: Calibrator, limits: RiskLimits
) -> Decision:
    q = calibrator.predict(candidate.raw_prob)
    hurdle = round_trip_cost(candidate.costs, candidate.notional_hint)
    gate = decide(candidate, q, hurdle)
    sizing = size(candidate, gate, limits)
    return Decision(gate=gate, sizing=sizing)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix develop --command uv run pytest tests/core/test_decision.py -v`
Expected: both `PASSED`.

- [ ] **Step 5: Run the full gate suite**

Run:
```bash
nix develop --command bash -c '
  uv run ruff check &&
  uv run ruff format --check &&
  uv run mypy &&
  uv run pytest --cov
'
```
Expected: ruff clean, format clean, mypy `Success`, all tests `passed`.

- [ ] **Step 6: Commit**

```bash
git add core/decision.py tests/core/test_decision.py
git commit -m "feat(core): pure decision pipeline composing the domain core"
```

---

## Final acceptance check (Phase 1 gate)

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
Expected: every command exits 0 — ruff clean, format clean, mypy `Success`, all unit + property tests `passed`.

Confirm the PLAN.md invariants are each proven by a test:
- gate never trades below cost → `test_cost_gate_is_law` (Task 4)
- size never exceeds caps or bankroll → `test_stake_never_exceeds_caps_or_bankroll` (Task 5)
- calibration monotonic + reduces Brier → `test_monotonic`, `test_isotonic_reduces_brier_on_overconfident_inputs` (Task 6)
- P&L accounting balances → `test_pnl_accounting_balances` (Task 7)

Then mark Phase 1 in `PLAN.md`:
- [ ] Change `- [ ] Phase 1 — Domain core` to `- [x] Phase 1 — Domain core`, commit with `docs: mark Phase 1 complete`.

This satisfies `PLAN.md`'s Phase 1 gate: "unit + property tests for each component. Key invariants proven: the gate never trades below cost; size never exceeds caps or bankroll; calibration is monotonic and reduces Brier on overconfident inputs; P&L accounting balances."
