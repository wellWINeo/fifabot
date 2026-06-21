# Phase 3 — Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build S1 (lag/divergence) and S2 (cross-market consistency) signal producers that feed the existing Phase 1 core through the Phase 2 harness, plus a typed LLM output contract (no agent).

**Architecture:** Pure signal math lives in `core/signals/` (operating on primitive floats, no IO imports); thin `Strategy` wrappers in `backtest/` read the as-of `MarketView`, call the pure functions, build a `TradeCandidate`, and run the existing `evaluate()` pipeline. Gating/sizing stay the core's job — signals only produce a fair probability or a structural abstain. `MarketGroup` (a Gamma negRisk event's mutually-exclusive legs) is the new grouping primitive S2 consumes.

**Tech Stack:** Python 3.12+, `uv`, `pydantic` v2, `numpy`/`scikit-learn` (already used by the core), `hypothesis` (property tests), `pytest`, `ruff`, `mypy`.

## Global Constraints

- **Python ≥ 3.12**, managed with `uv`; run everything inside `nix develop` via `uv run …`.
- **No new runtime dependency this phase** (the `pydantic-ai` agent is deferred to Phase 4). No `uv.lock` change.
- **TDD, red → green.** Write the failing test first; a step is done only when its tests pass AND `uv run ruff check`, `uv run ruff format --check`, and `uv run mypy` are clean.
- **No real network in any test.** Pure functions and fixtures only.
- **Pure core, thin edges.** `core/` must NOT import from `data/` or `backtest/`. Signal math in `core/signals/` takes primitive floats; the `MarketEvent`/`MarketView`-facing wrappers live in `backtest/`.
- **Cost gate is law / single-sourced.** Signals never re-implement the hurdle. Actionability is decided by `core.edge_gate` via `core.decision.evaluate`.
- **No look-ahead.** Reference/price queries go through `MarketView`, which raises `LookAheadError` for any `ts > as_of`.
- **Numeric types:** `Decimal` for prices/cash; `float` for statistical/decision math (`p_fair`, edges, Brier). Signal math functions take/return `float`; wrappers convert `Decimal`→`float` at the boundary (`float(event.quote.price)`).
- **Import placement (ruff E402):** when a step says "append to" or "extend the imports of" an existing file, put every new `import` / `from … import …` line in that file's **existing top-of-file import block** — never after a function or class. Mid-file imports fail `ruff check` (E402). Append only the new functions/classes/constants/tests below the existing code. Merge into the existing import line where one already imports from the same module.
- **Commit messages:** plain, no attribution trailers (no `Co-Authored-By`, no "Generated with…").

---

### Task 1: De-vig primitive

**Files:**
- Create: `core/signals/__init__.py` (empty)
- Create: `core/signals/devig.py`
- Create: `tests/core/signals/__init__.py` (empty)
- Test: `tests/core/signals/test_devig.py`

**Interfaces:**
- Produces: `overround(values: Sequence[float]) -> float`; `devig(values: Sequence[float]) -> list[float]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/signals/test_devig.py
import pytest
from hypothesis import given
from hypothesis import strategies as st

from core.signals.devig import devig, overround


def test_overround_sums_values() -> None:
    assert overround([0.5, 0.3, 0.28]) == pytest.approx(1.08)


def test_devig_normalizes_to_one() -> None:
    assert sum(devig([0.5, 0.3, 0.28])) == pytest.approx(1.0)


def test_devig_preserves_fair_two_way() -> None:
    assert devig([0.6, 0.6]) == pytest.approx([0.5, 0.5])


def test_devig_empty_raises() -> None:
    with pytest.raises(ValueError):
        devig([])


def test_devig_nonpositive_raises() -> None:
    with pytest.raises(ValueError):
        devig([0.5, 0.0])


@given(st.lists(st.floats(min_value=1e-6, max_value=10.0), min_size=1, max_size=8))
def test_devig_always_sums_to_one(values: list[float]) -> None:
    assert sum(devig(values)) == pytest.approx(1.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/signals/test_devig.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.signals'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/signals/devig.py
"""De-vig primitive: normalize values that should sum to 1.0.

Shared by S2 (Polymarket linked-group YES prices) and, in Phase 4, the
reference-odds adapter (decimal book odds passed as 1/odds). Pure float math.
"""

from __future__ import annotations

from collections.abc import Sequence


def overround(values: Sequence[float]) -> float:
    """Sum of values that should total ~1.0; deviation is the arbitrage edge."""
    if not values:
        raise ValueError("values must be non-empty")
    if any(v <= 0.0 for v in values):
        raise ValueError("values must be positive")
    return float(sum(values))


def devig(values: Sequence[float]) -> list[float]:
    """Normalize values to sum to 1.0, preserving their ratios."""
    total = overround(values)
    return [v / total for v in values]
```

Create `core/signals/__init__.py` and `tests/core/signals/__init__.py` as empty files.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/signals/test_devig.py -v && uv run ruff check && uv run mypy`
Expected: PASS; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add core/signals/__init__.py core/signals/devig.py tests/core/signals/__init__.py tests/core/signals/test_devig.py
git commit -m "feat(signals): de-vig primitive (overround + normalize)"
```

---

### Task 2: SignalOutput model

**Files:**
- Create: `core/signals/base.py`
- Test: `tests/core/signals/test_base.py`

**Interfaces:**
- Produces: `SignalOutput(p_fair: float, source: str, rationale: str, group_id: str | None = None, overround: float | None = None)` — frozen pydantic model; `p_fair` constrained to `[0, 1]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/signals/test_base.py
import pytest
from pydantic import ValidationError

from core.signals.base import SignalOutput


def test_signal_output_construction() -> None:
    out = SignalOutput(
        p_fair=0.62, source="S2", rationale="overround 1.05",
        group_id="evt-1", overround=1.05,
    )
    assert out.p_fair == 0.62
    assert out.group_id == "evt-1"


def test_signal_output_rejects_out_of_range_prob() -> None:
    with pytest.raises(ValidationError):
        SignalOutput(p_fair=1.5, source="S1", rationale="x")


def test_signal_output_is_frozen() -> None:
    out = SignalOutput(p_fair=0.5, source="S1", rationale="x")
    with pytest.raises(ValidationError):
        out.p_fair = 0.6  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/signals/test_base.py -v`
Expected: FAIL — `ImportError: cannot import name 'SignalOutput'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/signals/base.py
"""SignalOutput: the uniform per-market estimate a signal emits.

A signal abstains by returning None at the wrapper level; when it has an
opinion it produces a SignalOutput. group_id/overround carry S2 basket context
recorded for Phase 5 (not acted on in Phase 3).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SignalOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    p_fair: float = Field(ge=0.0, le=1.0)
    source: str
    rationale: str
    group_id: str | None = None
    overround: float | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/signals/test_base.py -v && uv run ruff check && uv run mypy`
Expected: PASS; clean.

- [ ] **Step 5: Commit**

```bash
git add core/signals/base.py tests/core/signals/test_base.py
git commit -m "feat(signals): SignalOutput model"
```

---

### Task 3: S2 cross-market consistency (pure)

**Files:**
- Create: `core/signals/consistency.py`
- Test: `tests/core/signals/test_consistency.py`

**Interfaces:**
- Consumes: `core.signals.devig.devig`, `core.signals.devig.overround`.
- Produces: `ConsistencyResult(overround: float, fair_legs: list[float])` (frozen dataclass); `scan_consistency(yes_prices: Sequence[float]) -> ConsistencyResult`.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/signals/test_consistency.py
import pytest

from core.signals.consistency import scan_consistency


def test_overround_basket_flagged() -> None:
    result = scan_consistency([0.50, 0.30, 0.28])
    assert result.overround == pytest.approx(1.08)
    assert sum(result.fair_legs) == pytest.approx(1.0)


def test_fair_legs_index_aligned() -> None:
    result = scan_consistency([0.50, 0.30, 0.28])
    assert result.fair_legs[2] < result.fair_legs[1] < result.fair_legs[0]


def test_balanced_group_overround_near_one() -> None:
    result = scan_consistency([0.34, 0.33, 0.33])
    assert result.overround == pytest.approx(1.0)
    assert result.fair_legs == pytest.approx([0.34, 0.33, 0.33])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/signals/test_consistency.py -v`
Expected: FAIL — `ModuleNotFoundError`/`ImportError` for `scan_consistency`.

- [ ] **Step 3: Write minimal implementation**

```python
# core/signals/consistency.py
"""S2 cross-market consistency: de-vig a mutually-exclusive group's YES prices.

`overround` deviating from 1.0 is the arbitrage edge; `fair_legs` are the
normalized per-leg fair probabilities, index-aligned with the input prices.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core.signals.devig import devig, overround


@dataclass(frozen=True)
class ConsistencyResult:
    overround: float
    fair_legs: list[float]


def scan_consistency(yes_prices: Sequence[float]) -> ConsistencyResult:
    return ConsistencyResult(
        overround=overround(yes_prices), fair_legs=devig(yes_prices)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/signals/test_consistency.py -v && uv run ruff check && uv run mypy`
Expected: PASS; clean.

- [ ] **Step 5: Commit**

```bash
git add core/signals/consistency.py tests/core/signals/test_consistency.py
git commit -m "feat(signals): S2 cross-market consistency scan"
```

---

### Task 4: S1 lag/divergence (pure)

**Files:**
- Create: `core/signals/divergence.py`
- Test: `tests/core/signals/test_divergence.py`

**Interfaces:**
- Produces: `DivergenceResult(fair: float, raw_edge: float)` (frozen dataclass); `divergence(pm_yes: float, ref_fair: float) -> DivergenceResult` where `raw_edge = ref_fair - pm_yes`.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/signals/test_divergence.py
import pytest

from core.signals.divergence import divergence


def test_positive_edge_when_pm_underprices() -> None:
    result = divergence(pm_yes=0.50, ref_fair=0.62)
    assert result.fair == 0.62
    assert result.raw_edge == pytest.approx(0.12)


def test_negative_edge_when_pm_overprices() -> None:
    assert divergence(pm_yes=0.70, ref_fair=0.60).raw_edge == pytest.approx(-0.10)


def test_zero_edge_when_aligned() -> None:
    assert divergence(pm_yes=0.55, ref_fair=0.55).raw_edge == pytest.approx(0.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/signals/test_divergence.py -v`
Expected: FAIL — import error for `divergence`.

- [ ] **Step 3: Write minimal implementation**

```python
# core/signals/divergence.py
"""S1 lag/divergence: compare a Polymarket YES price to a sharp reference fair.

Actionability (is the gap big enough) is the cost gate's job downstream; this
only measures the signed gap and surfaces the reference as the fair estimate.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DivergenceResult:
    fair: float
    raw_edge: float


def divergence(pm_yes: float, ref_fair: float) -> DivergenceResult:
    return DivergenceResult(fair=ref_fair, raw_edge=ref_fair - pm_yes)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/signals/test_divergence.py -v && uv run ruff check && uv run mypy`
Expected: PASS; clean.

- [ ] **Step 5: Commit**

```bash
git add core/signals/divergence.py tests/core/signals/test_divergence.py
git commit -m "feat(signals): S1 lag/divergence measure"
```

---

### Task 5: MarketGroup canonical model

**Files:**
- Modify: `data/events.py`
- Test: `tests/data/test_events.py` (append)

**Interfaces:**
- Produces: `MarketGroup(group_id: str, market_ids: tuple[str, ...], kind: str = "negrisk")` — frozen; rejects fewer than two legs.

- [ ] **Step 1: Write the failing test** (append to `tests/data/test_events.py`)

```python
import pytest
from pydantic import ValidationError

from data.events import MarketGroup


def test_market_group_construction() -> None:
    group = MarketGroup(group_id="30615", market_ids=("558934", "558935"))
    assert group.market_ids == ("558934", "558935")
    assert group.kind == "negrisk"


def test_market_group_requires_two_legs() -> None:
    with pytest.raises(ValidationError):
        MarketGroup(group_id="g", market_ids=("only-one",))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/data/test_events.py -v -k market_group`
Expected: FAIL — `ImportError: cannot import name 'MarketGroup'`

- [ ] **Step 3: Write minimal implementation** (append to `data/events.py`)

```python
from pydantic import field_validator  # add to existing pydantic import line


class MarketGroup(BaseModel):
    """A set of mutually-exclusive YES legs (one Gamma negRisk event)."""

    model_config = ConfigDict(frozen=True)

    group_id: str
    market_ids: tuple[str, ...]
    kind: str = "negrisk"

    @field_validator("market_ids")
    @classmethod
    def _at_least_two_legs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) < 2:
            raise ValueError("a market group needs at least two legs")
        return value
```

Note: extend the existing `from pydantic import …` line in `data/events.py` to include `field_validator` rather than adding a second import.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/data/test_events.py -v && uv run ruff check && uv run mypy`
Expected: PASS; clean.

- [ ] **Step 5: Commit**

```bash
git add data/events.py tests/data/test_events.py
git commit -m "feat(data): MarketGroup canonical model"
```

---

### Task 6: Gamma event payloads + negRisk group parser

**Files:**
- Modify: `data/payloads.py`
- Modify: `data/gamma.py`
- Create: `tests/fixtures/gamma/events_negrisk.json`
- Test: `tests/data/test_gamma.py` (append)

**Interfaces:**
- Consumes: `data.events.MarketGroup`.
- Produces: `GammaEventMarket` and `GammaEvent` payload models; `parse_event_groups(raw: GammaEvent) -> list[MarketGroup]` (returns one group per negRisk event with ≥2 markets, else `[]`).

Placement note: `parse_event_groups` goes in `data/gamma.py` beside the other Gamma parsers (not in `data/events.py` as the spec sketched) to match the Phase 2 convention where all Gamma parsing lives in `data/gamma.py`. Token-id decoding (`clobTokenIds`) is NOT needed for grouping and is deferred to Phase 4/5.

- [ ] **Step 1: Write the failing test + fixture**

Create `tests/fixtures/gamma/events_negrisk.json` (shape captured from the live Gamma probe; token ids shortened — the parser ignores them):

```json
[
  {
    "id": "30615",
    "slug": "world-cup-winner",
    "title": "World Cup Winner",
    "negRisk": true,
    "enableNegRisk": true,
    "markets": [
      {"id": "558934", "question": "Will Spain win the 2026 FIFA World Cup?", "groupItemTitle": "Spain", "outcomes": "[\"Yes\", \"No\"]", "clobTokenIds": "[\"4394\", \"1126\"]"},
      {"id": "558935", "question": "Will England win the 2026 FIFA World Cup?", "groupItemTitle": "England", "outcomes": "[\"Yes\", \"No\"]", "clobTokenIds": "[\"1155\", \"7712\"]"},
      {"id": "558957", "question": "Will New Zealand win the 2026 FIFA World Cup?", "groupItemTitle": "New Zealand", "outcomes": "[\"Yes\", \"No\"]", "clobTokenIds": "[\"7960\", \"1930\"]"}
    ]
  },
  {
    "id": "16183",
    "slug": "kraken-ipo",
    "title": "Kraken IPO by ___ ?",
    "negRisk": false,
    "enableNegRisk": false,
    "markets": [
      {"id": "516950", "question": "Kraken IPO in 2025?", "groupItemTitle": "December 31", "outcomes": "[\"Yes\", \"No\"]", "clobTokenIds": "[\"1062\", \"3300\"]"},
      {"id": "678876", "question": "Kraken IPO by March 31, 2026?", "groupItemTitle": "March 31, 2026", "outcomes": "[\"Yes\", \"No\"]", "clobTokenIds": "[\"3379\", \"1036\"]"}
    ]
  }
]
```

Append to `tests/data/test_gamma.py`:

```python
from data.events import MarketGroup
from data.gamma import parse_event_groups
from data.payloads import GammaEvent


def _load_events() -> list[GammaEvent]:
    raw = json.loads((_FIX / "events_negrisk.json").read_text())
    return [GammaEvent.model_validate(event) for event in raw]


def test_parse_event_groups_extracts_negrisk_group() -> None:
    groups = [g for event in _load_events() for g in parse_event_groups(event)]
    assert len(groups) == 1
    assert groups[0] == MarketGroup(
        group_id="30615",
        market_ids=("558934", "558935", "558957"),
        kind="negrisk",
    )


def test_parse_event_groups_skips_non_negrisk() -> None:
    kraken = next(e for e in _load_events() if e.id == "16183")
    assert parse_event_groups(kraken) == []
```

(`_FIX` and `json` are already imported at the top of `tests/data/test_gamma.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/data/test_gamma.py -v -k event_groups`
Expected: FAIL — `ImportError: cannot import name 'GammaEvent'`

- [ ] **Step 3: Write minimal implementation**

Append to `data/payloads.py`:

```python
class GammaEventMarket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    question: str
    groupItemTitle: str = ""  # noqa: N815


class GammaEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    slug: str = ""
    title: str = ""
    negRisk: bool = False  # noqa: N815
    enableNegRisk: bool = False  # noqa: N815
    markets: list[GammaEventMarket] = Field(default_factory=list)
```

Append to `data/gamma.py` (and extend its imports: add `MarketGroup` from `data.events` and `GammaEvent` from `data.payloads`):

```python
def parse_event_groups(raw: GammaEvent) -> list[MarketGroup]:
    """Turn a Gamma event into mutually-exclusive MarketGroups.

    Only negRisk events describe a set of mutually-exclusive YES legs whose
    prices should sum to ~1.0 — S2's target. Non-negRisk events and events with
    fewer than two markets yield nothing.
    """
    if not (raw.enableNegRisk or raw.negRisk):
        return []
    market_ids = tuple(market.id for market in raw.markets)
    if len(market_ids) < 2:
        return []
    return [MarketGroup(group_id=raw.id, market_ids=market_ids, kind="negrisk")]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/data/test_gamma.py -v && uv run ruff check && uv run mypy`
Expected: PASS; clean.

- [ ] **Step 5: Commit**

```bash
git add data/payloads.py data/gamma.py tests/fixtures/gamma/events_negrisk.json tests/data/test_gamma.py
git commit -m "feat(data): parse Gamma negRisk events into MarketGroups"
```

---

### Task 7: Surface the calibrated probability on Decision

**Files:**
- Modify: `core/models.py` (the `Decision` model)
- Modify: `core/decision.py` (`evaluate`)
- Test: `tests/core/test_decision.py` (append)

**Interfaces:**
- Produces: `Decision.prob: float | None` (defaults to `None`; `evaluate` sets it to the calibrated probability `q`). Backward compatible — existing `Decision(gate=…, sizing=…)` construction still valid.

- [ ] **Step 1: Write the failing test** (append to `tests/core/test_decision.py`)

```python
def test_evaluate_records_calibrated_probability() -> None:
    decision = evaluate(_candidate("0.01"), _FixedCalibrator(0.70), _limits())
    assert decision.prob == pytest.approx(0.70)
```

Add `import pytest` at the top of the file (it is not currently imported).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_decision.py::test_evaluate_records_calibrated_probability -v`
Expected: FAIL — `AttributeError: 'Decision' object has no attribute 'prob'`

- [ ] **Step 3: Write minimal implementation**

In `core/models.py`, add the field to `Decision`:

```python
class Decision(BaseModel):
    model_config = ConfigDict(frozen=True)

    gate: GateResult
    sizing: SizingResult
    prob: float | None = None
```

In `core/decision.py`, set it in `evaluate` (the function already computes `q`):

```python
    gate = decide(candidate, q, hurdle)
    sizing = size(candidate, gate, limits)
    return Decision(gate=gate, sizing=sizing, prob=q)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_decision.py -v && uv run ruff check && uv run mypy`
Expected: PASS (all existing decision tests still pass); clean.

- [ ] **Step 5: Commit**

```bash
git add core/models.py core/decision.py tests/core/test_decision.py
git commit -m "feat(core): record calibrated probability on Decision"
```

---

### Task 8: DivergenceStrategy (S1 harness wrapper)

**Files:**
- Create: `backtest/signals.py`
- Test: `tests/backtest/test_signals.py`

**Interfaces:**
- Consumes: `backtest.view.MarketView`, `core.calibration.Calibrator`, `core.decision.evaluate`, `core.models.{CostInputs, Decision, RiskLimits, TradeCandidate}`, `core.signals.divergence.divergence`, `data.events.MarketEvent`.
- Produces: `DivergenceStrategy(*, costs: CostInputs, notional_hint: Decimal, calibrator: Calibrator, limits: RiskLimits)` implementing the `Strategy` Protocol (`on_event(event, view) -> Decision | None`). Returns `None` when no reference is available; otherwise runs `evaluate`.

Note: a "thin reference / low-liquidity" abstain is deferred to Phase 4 — the `ReferencePrice` Protocol exposes only a price, no depth. Phase 3's structural abstain is "no reference available".

- [ ] **Step 1: Write the failing test**

```python
# tests/backtest/test_signals.py
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from backtest.signals import DivergenceStrategy
from backtest.view import MarketView
from core.models import CalibrationSample, CostInputs, RiskLimits, Side
from data.events import Quote, event_from_quote
from data.reference import ReplayReference

_TS = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


class _Identity:
    """Calibrator test double: predict(x) == x."""

    def fit(self, samples: Sequence[CalibrationSample]) -> None:
        return None

    def predict(self, raw: float) -> float:
        return raw


def _costs(spread: str = "0.01") -> CostInputs:
    return CostInputs(
        spread=Decimal(spread),
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


def _divergence_strategy(spread: str = "0.01") -> DivergenceStrategy:
    return DivergenceStrategy(
        costs=_costs(spread),
        notional_hint=Decimal("10"),
        calibrator=_Identity(),
        limits=_limits(),
    )


def _view(pm_price: str, ref_price: str | None) -> tuple[MarketView, Quote]:
    quote = Quote(market_id="m", ts=_TS, price=Decimal(pm_price))
    ref = (
        ReplayReference([Quote(market_id="m", ts=_TS, price=Decimal(ref_price))])
        if ref_price is not None
        else ReplayReference([])
    )
    return MarketView(_TS, {"m": [quote]}, ref), quote


def test_divergence_acts_when_reference_diverges() -> None:
    view, quote = _view("0.50", "0.70")
    decision = _divergence_strategy().on_event(event_from_quote(quote), view)
    assert decision is not None
    assert decision.gate.action == "act"
    assert decision.gate.side is Side.BUY_YES


def test_divergence_abstains_when_aligned() -> None:
    view, quote = _view("0.50", "0.50")
    decision = _divergence_strategy().on_event(event_from_quote(quote), view)
    assert decision is not None
    assert decision.gate.action == "abstain"


def test_divergence_returns_none_without_reference() -> None:
    view, quote = _view("0.50", None)
    assert _divergence_strategy().on_event(event_from_quote(quote), view) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/backtest/test_signals.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backtest.signals'`

- [ ] **Step 3: Write minimal implementation**

```python
# backtest/signals.py
"""Phase 3 signal strategies: thin Strategy wrappers over the pure core.

They read the as-of MarketView, call the pure signal math, build a
TradeCandidate, and run the Phase 1 decision pipeline. No financial logic lives
here — gating/sizing stay the core's job.
"""

from __future__ import annotations

from decimal import Decimal

from backtest.view import MarketView
from core.calibration import Calibrator
from core.decision import evaluate
from core.models import CostInputs, Decision, RiskLimits, TradeCandidate
from core.signals.divergence import divergence
from data.events import MarketEvent


class DivergenceStrategy:
    """S1: act when Polymarket diverges from the sharp reference fair price."""

    def __init__(
        self,
        *,
        costs: CostInputs,
        notional_hint: Decimal,
        calibrator: Calibrator,
        limits: RiskLimits,
    ) -> None:
        self._costs = costs
        self._notional = notional_hint
        self._calibrator = calibrator
        self._limits = limits

    def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
        ref = view.reference_at(event.market_id, event.ts)
        if ref is None:
            return None
        result = divergence(float(event.quote.price), float(ref))
        candidate = TradeCandidate(
            price=event.quote.price,
            raw_prob=result.fair,
            costs=self._costs,
            notional_hint=self._notional,
        )
        return evaluate(candidate, self._calibrator, self._limits)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/backtest/test_signals.py -v && uv run ruff check && uv run mypy`
Expected: PASS; clean.

- [ ] **Step 5: Commit**

```bash
git add backtest/signals.py tests/backtest/test_signals.py
git commit -m "feat(backtest): DivergenceStrategy (S1) harness wrapper"
```

---

### Task 9: ConsistencyStrategy (S2 harness wrapper)

**Files:**
- Modify: `backtest/signals.py`
- Test: `tests/backtest/test_signals.py` (append)

**Interfaces:**
- Consumes: everything Task 8 imports, plus `core.signals.consistency.scan_consistency`, `data.events.MarketGroup`, `collections.abc.Sequence`.
- Produces: `ConsistencyStrategy(*, groups: Sequence[MarketGroup], costs: CostInputs, notional_hint: Decimal, calibrator: Calibrator, limits: RiskLimits)` implementing the `Strategy` Protocol. Returns `None` when the event's market is not in any group or the group is incomplete in the view.

- [ ] **Step 1: Write the failing test** (append to `tests/backtest/test_signals.py`)

```python
from backtest.signals import ConsistencyStrategy
from data.events import MarketGroup

_GROUP = MarketGroup(group_id="g", market_ids=("a", "b", "c"))


def _consistency_strategy() -> ConsistencyStrategy:
    return ConsistencyStrategy(
        groups=[_GROUP],
        costs=_costs("0.01"),
        notional_hint=Decimal("10"),
        calibrator=_Identity(),
        limits=_limits(),
    )


def _group_view(prices: dict[str, str]) -> MarketView:
    quotes = {
        mid: [Quote(market_id=mid, ts=_TS, price=Decimal(p))]
        for mid, p in prices.items()
    }
    return MarketView(_TS, quotes, None)


def test_consistency_acts_on_overround_basket() -> None:
    # legs sum to 1.08 -> each YES overpriced -> de-vigged fair < price -> sell YES
    view = _group_view({"a": "0.50", "b": "0.30", "c": "0.28"})
    event = event_from_quote(Quote(market_id="a", ts=_TS, price=Decimal("0.50")))
    decision = _consistency_strategy().on_event(event, view)
    assert decision is not None
    assert decision.gate.action == "act"
    assert decision.gate.side is Side.BUY_NO


def test_consistency_abstains_on_balanced_basket() -> None:
    view = _group_view({"a": "0.34", "b": "0.33", "c": "0.33"})
    event = event_from_quote(Quote(market_id="a", ts=_TS, price=Decimal("0.34")))
    decision = _consistency_strategy().on_event(event, view)
    assert decision is not None
    assert decision.gate.action == "abstain"


def test_consistency_returns_none_for_incomplete_group() -> None:
    view = _group_view({"a": "0.50", "b": "0.30"})  # leg "c" missing
    event = event_from_quote(Quote(market_id="a", ts=_TS, price=Decimal("0.50")))
    assert _consistency_strategy().on_event(event, view) is None


def test_consistency_returns_none_for_unknown_market() -> None:
    view = _group_view({"z": "0.50"})
    event = event_from_quote(Quote(market_id="z", ts=_TS, price=Decimal("0.50")))
    assert _consistency_strategy().on_event(event, view) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/backtest/test_signals.py -v -k consistency`
Expected: FAIL — `ImportError: cannot import name 'ConsistencyStrategy'`

- [ ] **Step 3: Write minimal implementation** (append to `backtest/signals.py`; extend imports with `Sequence`, `scan_consistency`, `MarketGroup`)

```python
from collections.abc import Sequence  # add to imports

from core.signals.consistency import scan_consistency  # add to imports
from data.events import MarketEvent, MarketGroup  # extend existing MarketEvent import


class ConsistencyStrategy:
    """S2: de-vig a market's mutually-exclusive group; act on per-leg mispricing."""

    def __init__(
        self,
        *,
        groups: Sequence[MarketGroup],
        costs: CostInputs,
        notional_hint: Decimal,
        calibrator: Calibrator,
        limits: RiskLimits,
    ) -> None:
        self._group_of: dict[str, MarketGroup] = {}
        for group in groups:
            for market_id in group.market_ids:
                self._group_of[market_id] = group
        self._costs = costs
        self._notional = notional_hint
        self._calibrator = calibrator
        self._limits = limits

    def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
        group = self._group_of.get(event.market_id)
        if group is None:
            return None
        prices: list[float] = []
        for market_id in group.market_ids:
            price = view.latest_price(market_id)
            if price is None:
                return None
            prices.append(float(price))
        result = scan_consistency(prices)
        idx = group.market_ids.index(event.market_id)
        candidate = TradeCandidate(
            price=event.quote.price,
            raw_prob=result.fair_legs[idx],
            costs=self._costs,
            notional_hint=self._notional,
        )
        return evaluate(candidate, self._calibrator, self._limits)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/backtest/test_signals.py -v && uv run ruff check && uv run mypy`
Expected: PASS; clean.

- [ ] **Step 5: Commit**

```bash
git add backtest/signals.py tests/backtest/test_signals.py
git commit -m "feat(backtest): ConsistencyStrategy (S2) harness wrapper"
```

---

### Task 10: Engine surfaces signal probabilities

**Files:**
- Modify: `backtest/engine.py` (`BacktestResult`, `replay`)
- Test: `tests/backtest/test_engine.py` (append)

**Interfaces:**
- Consumes: `Decision.prob` (Task 7), `ConsistencyStrategy` (Task 9).
- Produces: `BacktestResult.signal_probs: tuple[tuple[str, float], ...]` (defaults to `()`), recording `(market_id, prob)` for every event whose strategy returned a `Decision` carrying a non-`None` `prob`.

- [ ] **Step 1: Write the failing test** (append to `tests/backtest/test_engine.py`)

This is also the "signals run in the harness" integration check: a `ConsistencyStrategy` driven through `replay` over a synthetic overround basket records the probabilities it computed.

```python
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from backtest.engine import replay
from backtest.signals import ConsistencyStrategy
from core.models import CalibrationSample, CostInputs, RiskLimits
from data.events import MarketGroup, Quote, event_from_quote

_T0 = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


class _Id:
    def fit(self, samples: Sequence[CalibrationSample]) -> None:
        return None

    def predict(self, raw: float) -> float:
        return raw


def test_replay_records_signal_probs() -> None:
    group = MarketGroup(group_id="g", market_ids=("a", "b", "c"))
    prices = {"a": "0.50", "b": "0.30", "c": "0.28"}
    # In event-driven replay a leg's group is only complete once every leg has
    # appeared. Legs arrive a, b, c (group completes at c); then a re-quotes so
    # leg "a" is also evaluated against the now-complete group.
    schedule = [
        ("a", _T0),
        ("b", _T0 + timedelta(minutes=1)),
        ("c", _T0 + timedelta(minutes=2)),
        ("a", _T0 + timedelta(minutes=3)),
    ]
    events = [
        event_from_quote(Quote(market_id=mid, ts=ts, price=Decimal(prices[mid])))
        for mid, ts in schedule
    ]
    limits = RiskLimits(
        bankroll=Decimal("25"),
        kelly_fraction=0.25,
        max_position_fraction=0.2,
        max_position_usd=Decimal("5"),
    )
    strategy = ConsistencyStrategy(
        groups=[group],
        costs=CostInputs(
            spread=Decimal("0.01"),
            fee_rate=Decimal("0"),
            gas_usd=Decimal("0"),
            model_error_margin=Decimal("0"),
        ),
        notional_hint=Decimal("10"),
        calibrator=_Id(),
        limits=limits,
    )
    result = replay(events, strategy, limits)
    recorded = dict(result.signal_probs)
    # the de-vigged fair for each leg evaluated against the full basket (sum 1.08)
    assert recorded["c"] == pytest.approx(0.28 / 1.08)
    assert recorded["a"] == pytest.approx(0.50 / 1.08)
```

Add `import pytest` at the top of `tests/backtest/test_engine.py` if not already present.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/backtest/test_engine.py::test_replay_records_signal_probs -v`
Expected: FAIL — `AttributeError: 'BacktestResult' object has no attribute 'signal_probs'`

- [ ] **Step 3: Write minimal implementation**

In `backtest/engine.py`, add the field to `BacktestResult`:

```python
@dataclass(frozen=True)
class BacktestResult:
    fills: tuple[Fill, ...]
    realized_pnl: Decimal
    roi: float
    signal_probs: tuple[tuple[str, float], ...] = ()
```

In `replay`, collect probabilities. **Insert** into the existing loop — do not replace the existing position open/close logic. Add a list before the loop, append inside it right after `decision = strategy.on_event(...)`, and pass it to the existing `return`. The snippet below shows only the surrounding context, not the full loop body:

```python
    fills: list[Fill] = []
    deployed = Decimal(0)
    signal_probs: list[tuple[str, float]] = []

    for event in ordered:
        quotes_by_market.setdefault(event.market_id, []).append(event.quote)
        view = MarketView(event.ts, quotes_by_market, reference)
        decision = strategy.on_event(event, view)

        if decision is not None and decision.prob is not None:
            signal_probs.append((event.market_id, decision.prob))
```

And in the final `return BacktestResult(...)`, add `signal_probs=tuple(signal_probs)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/backtest/ -v && uv run ruff check && uv run mypy`
Expected: PASS (existing engine determinism tests still pass — the new field defaults to `()` and existing synthetic strategies set no `prob`); clean.

- [ ] **Step 5: Commit**

```bash
git add backtest/engine.py tests/backtest/test_engine.py
git commit -m "feat(backtest): surface signal probabilities from replay"
```

---

### Task 11: Walk-forward Brier / calibration scoring

**Files:**
- Modify: `backtest/report.py`
- Test: `tests/backtest/test_report.py` (append)

**Interfaces:**
- Consumes: `core.metrics.brier_score`, `core.metrics.calibration_curve`, `core.models.CalibrationSample`.
- Produces: `SignalScore(brier: float, curve: list[tuple[float, float, int]])` (frozen dataclass); `score_signals(samples: Sequence[CalibrationSample], *, bins: int = 10) -> SignalScore`; `calibration_samples(signal_probs: Sequence[tuple[str, float]], outcomes: Mapping[str, int]) -> list[CalibrationSample]` (joins recorded probs with resolved 0/1 outcomes, dropping markets without an outcome label).

- [ ] **Step 1: Write the failing test** (append to `tests/backtest/test_report.py`)

```python
from collections.abc import Mapping

from backtest.report import SignalScore, calibration_samples, score_signals
from core.models import CalibrationSample


def test_calibration_samples_joins_probs_with_outcomes() -> None:
    probs = [("a", 0.8), ("b", 0.3), ("c", 0.5)]
    outcomes: Mapping[str, int] = {"a": 1, "b": 0}  # "c" unlabeled -> dropped
    samples = calibration_samples(probs, outcomes)
    assert samples == [
        CalibrationSample(raw_prob=0.8, outcome=1),
        CalibrationSample(raw_prob=0.3, outcome=0),
    ]


def test_score_signals_perfect_predictions_zero_brier() -> None:
    samples = [
        CalibrationSample(raw_prob=1.0, outcome=1),
        CalibrationSample(raw_prob=0.0, outcome=0),
    ]
    score = score_signals(samples, bins=2)
    assert isinstance(score, SignalScore)
    assert score.brier == 0.0


def test_score_signals_overconfident_wrong_high_brier() -> None:
    samples = [CalibrationSample(raw_prob=1.0, outcome=0)]
    assert score_signals(samples, bins=2).brier == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/backtest/test_report.py -v -k "signals or calibration_samples"`
Expected: FAIL — `ImportError: cannot import name 'score_signals'`

- [ ] **Step 3: Write minimal implementation** (append to `backtest/report.py`; extend imports)

```python
from collections.abc import Mapping, Sequence  # extend existing import

from core.metrics import brier_score, calibration_curve
from core.models import CalibrationSample


@dataclass(frozen=True)
class SignalScore:
    brier: float
    curve: list[tuple[float, float, int]]


def calibration_samples(
    signal_probs: Sequence[tuple[str, float]], outcomes: Mapping[str, int]
) -> list[CalibrationSample]:
    """Join recorded (market_id, prob) with resolved 0/1 outcomes.

    Markets without an outcome label are dropped — outcomes come from match
    resolution and are used only post-hoc, never during a decision.
    """
    return [
        CalibrationSample(raw_prob=prob, outcome=outcomes[market_id])
        for market_id, prob in signal_probs
        if market_id in outcomes
    ]


def score_signals(
    samples: Sequence[CalibrationSample], *, bins: int = 10
) -> SignalScore:
    probs = [s.raw_prob for s in samples]
    obs = [s.outcome for s in samples]
    return SignalScore(
        brier=brier_score(probs, obs), curve=calibration_curve(probs, obs, bins)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/backtest/test_report.py -v && uv run ruff check && uv run mypy`
Expected: PASS; clean.

- [ ] **Step 5: Commit**

```bash
git add backtest/report.py tests/backtest/test_report.py
git commit -m "feat(backtest): Brier/calibration scoring over signal probabilities"
```

---

### Task 12: LLM output contract (schema only)

**Files:**
- Create: `llm/schema.py`
- Create: `tests/llm/__init__.py` (empty)
- Test: `tests/llm/test_schema.py`

**Interfaces:**
- Produces: `HypothesisOutput(p_fair: float, confidence: float, rationale: str)` — frozen, `extra="forbid"`, both floats constrained to `[0, 1]`. No `pydantic-ai` import. This is the typed shape the Phase 4 agent will populate; malformed/extra data raises `ValidationError`.

- [ ] **Step 1: Write the failing test**

```python
# tests/llm/test_schema.py
import pytest
from pydantic import ValidationError

from llm.schema import HypothesisOutput


def test_valid_output_parses() -> None:
    out = HypothesisOutput.model_validate(
        {"p_fair": 0.61, "confidence": 0.4, "rationale": "lineup news"}
    )
    assert out.p_fair == 0.61


def test_missing_field_rejected() -> None:
    with pytest.raises(ValidationError):
        HypothesisOutput.model_validate({"p_fair": 0.61, "confidence": 0.4})


def test_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        HypothesisOutput.model_validate(
            {"p_fair": 0.6, "confidence": 0.4, "rationale": "x", "stray": 1}
        )


def test_out_of_range_prob_rejected() -> None:
    with pytest.raises(ValidationError):
        HypothesisOutput.model_validate(
            {"p_fair": 1.4, "confidence": 0.4, "rationale": "x"}
        )
```

Create `tests/llm/__init__.py` as an empty file.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/llm/test_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm.schema'`

- [ ] **Step 3: Write minimal implementation**

```python
# llm/schema.py
"""Typed contract for the deferred LLM layer (Phase 4 builds the agent).

Defines the shape the pydantic-ai hypothesis generator / feature extractor will
emit. No agent and no pydantic-ai dependency in Phase 3 — only the validated
output type, so malformed model output is rejected cleanly at the boundary.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class HypothesisOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    p_fair: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/llm/test_schema.py -v && uv run ruff check && uv run mypy`
Expected: PASS; clean.

- [ ] **Step 5: Commit**

```bash
git add llm/schema.py tests/llm/__init__.py tests/llm/test_schema.py
git commit -m "feat(llm): typed HypothesisOutput contract (agent deferred to Phase 4)"
```

---

### Task 13: Re-scope PLAN.md (LLM robustness → Phase 4) and mark progress

**Files:**
- Modify: `PLAN.md`

**Interfaces:** none (documentation).

- [ ] **Step 1: Edit the Phase 3 gate** — in `PLAN.md`, replace the Phase 3 **Gate** paragraph:

> **Gate:** each signal flags known synthetic mispricings and abstains within
> noise; LLM output is schema-validated, mocked in tests, and malformed responses
> never crash the loop.

with:

> **Gate:** each signal flags known synthetic mispricings and abstains within
> noise; the LLM output contract is schema-validated. (The `pydantic-ai` agent —
> mocked in tests, with malformed responses never crashing the loop — is built in
> Phase 4.)

- [ ] **Step 2: Extend the Phase 4 deliverables + gate** — in the Phase 4 section, append to **Deliverables** "; the `pydantic-ai` hypothesis generator + feature extractor (the agent behind the Phase 3 output contract)", and append to its **Gate** ": the LLM agent runs behind a mock, its output is schema-validated, and malformed responses never crash the loop."

- [ ] **Step 3: Run the full suite + gates**

Run: `uv run pytest --cov && uv run ruff check && uv run ruff format --check && uv run mypy`
Expected: PASS; clean. (No code changed — this confirms the whole Phase 3 suite is green together.)

- [ ] **Step 4: Commit**

```bash
git add PLAN.md
git commit -m "docs: re-scope LLM robustness gate from Phase 3 to Phase 4"
```

---

## Self-Review

**Spec coverage** (each spec component → task):
- `core/signals/devig.py` → Task 1 ✓
- `core/signals/base.py` (`SignalOutput`) → Task 2 ✓
- `core/signals/consistency.py` (S2) → Task 3 ✓
- `core/signals/divergence.py` (S1) → Task 4 ✓
- `MarketGroup` model → Task 5 ✓
- `parse_event_groups` + Gamma event payloads + recorded fixture → Task 6 ✓
- `DivergenceStrategy` / `ConsistencyStrategy` (harness wiring) → Tasks 8, 9 ✓
- Brier/calibration report extension + engine surfacing probabilities → Tasks 7, 10, 11 ✓
- `llm/schema.py` typed contract → Task 12 ✓
- PLAN.md re-scope → Task 13 ✓
- No-look-ahead preserved → reference goes through `MarketView.reference_at`; the existing `MarketView`/`feed` look-ahead tests remain green (verified in Task 10's full-suite run).

**Deviations from the committed spec** (intentional, surfaced to the user):
1. **Thin-reference abstain deferred to Phase 4** (Task 8 note): the `ReferencePrice` Protocol exposes only a price, no depth, so "reference too thin" can't be implemented in Phase 3. Phase 3 abstains on "no reference available".
2. **`parse_event_groups` lives in `data/gamma.py`** (not `data/events.py`), matching the Phase 2 convention that all Gamma parsing lives with the Gamma adapter.
3. **`clobTokenIds` not decoded** in Phase 3 — grouping needs only market ids; YES/NO token decoding is a Phase 4/5 (execution) concern.
4. **Signal math signatures use `float`, not `Decimal`** — the spec sketched `Sequence[Decimal]`/`Decimal` inputs; the plan takes `float` and converts at the wrapper boundary, per the "float for decision math" rule.

**Placeholder scan:** none — every code/test step has complete content.

**Type consistency:** `Decision.prob` (Task 7) is consumed by `replay` (Task 10) and produced into `BacktestResult.signal_probs`, which `calibration_samples` (Task 11) consumes — names and `tuple[str, float]` shape match across tasks. `MarketGroup` fields (Task 5) match their use in Task 6 (`parse_event_groups`) and Task 9 (`ConsistencyStrategy`). `scan_consistency`/`divergence` return types match their wrapper use.

## Notes for the implementer

- Run everything inside `nix develop` with the `uv run` prefix.
- Tasks are ordered by dependency; do them in sequence. Tasks 1–4 (pure core) and 5–6 (data) are independent of each other but all precede the wrappers (8–9), which precede the engine/report wiring (10–11). Task 7 precedes Task 10.
- Each task is self-contained and ends green (tests + ruff + mypy) before the next.
