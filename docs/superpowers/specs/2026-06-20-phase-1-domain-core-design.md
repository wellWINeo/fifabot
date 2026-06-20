# Phase 1 — Domain core (design)

Date: 2026-06-20
Status: approved (design); implementation pending

## Context

`PLAN.md` Phase 1 builds the financial logic that decides **whether** and **how
much** to trade, fully isolated from network and frameworks. Per `CLAUDE.md`
this is the "pure core" of the hexagon — unit- and property-tested in isolation —
and it is where correctness matters most: these invariants are what prevent
losing money. Phase 0 (foundation) is complete: a nix-managed uv project with the
flat module skeleton (`core/ data/ backtest/ llm/ execution/ app/`), strict
`mypy`, `ruff`, `pytest` + `pytest-cov` + `hypothesis`, and CI/pre-commit gates.

**Goal:** typed domain models plus five pure components — cost model, edge gate
(abstain-by-default), position sizing (fractional Kelly + hard caps), calibration
(isotonic / Platt), and metrics (Brier, calibration curve, P&L, ROI) — composed
by a single pure pipeline, with every PLAN.md invariant proven by property tests.

This spec records the shape so the implementation plan executes it without
re-deciding.

## Decisions

- **Architecture: pipeline of pure functions + a thin compose.** Each component
  is a module of pure functions over pydantic models; `core/decision.py`
  `evaluate(...)` wires them (calibrate → edge → cost gate → size). Chosen over a
  stateful `Strategy` object or a monolithic `decide()` because it maps 1:1 onto
  PLAN.md's per-component invariants and keeps each unit independently testable.
  This `evaluate` is **pure** — it is *not* Phase 4 assembly (which wires live
  signals, IO, and asyncio). Phase 1 has no network.
- **Numeric representation: Decimal at the boundary, float inside.** `Decimal`
  for anything that becomes an order price, share quantity, or cash amount — so
  prices snap exactly to `tick_size` and P&L accounting balances to the cent.
  `float` for the decision/statistical math (edge, cost hurdle, Kelly fraction,
  calibration, Brier). Conversion is explicit at the gate/sizing boundary.
- **Calibration: scikit-learn, both isotonic and Platt**, behind one `Calibrator`
  interface. `IsotonicRegression(out_of_bounds="clip")` and a logistic (Platt)
  fit. Battle-tested over hand-rolled PAVA; matches `CLAUDE.md`.
- **Data models: minimal, demand-driven.** Model only what the five components
  consume/produce. Polymarket market structure (token IDs, order-book depth,
  linked-market groups for arbitrage) is deferred to the data adapters (Phase 2)
  and signals (Phase 3) that first need it.
- **Sizing risk model: Kelly on fair value vs price** (`f* = (q − p)/(1 − p)`),
  even though positions are normally closed before resolution. Closing early only
  reduces variance, so Kelly-to-fair-value is a conservative, principled size.
  Sizing to expected price-convergence distance would need a horizon model we do
  not have yet; deferred.
- **New dependencies: `numpy` and `scikit-learn`** as runtime deps — Phase 1 is
  the first phase that uses them (both directly imported by `calibration`).
  `scipy` arrives transitively via scikit-learn and is **not** declared directly
  until something imports it (YAGNI). `uv lock` before any commit so CI `--frozen`
  stays green.

## Components

All live under `core/`. Every pure function is total over its validated inputs;
invalid inputs are rejected at construction (pydantic) or raise explicitly.

### 1. `core/models.py` — typed domain models

pydantic v2, `frozen=True`, validated at construction:

- `Side` — enum: `BUY_YES`, `BUY_NO`.
- Prices are `Decimal` fields validated in `(0, 1)` and as an integer multiple of
  `tick_size` (default `0.01`). Off-tick values are rejected. `0` and `1` are
  excluded so the Kelly denominators (`1 − p` for YES, `p` for NO) are never zero.
- `CostInputs` — `spread: Decimal`, `fee_rate: Decimal` (Polymarket is 0% today
  but parameterized), `gas_usd: Decimal`, `model_error_margin: Decimal`. All
  `≥ 0`.
- `TradeCandidate` — `price: Decimal` (the YES-token price, validated in `(0, 1)`
  and on `tick_size`), `raw_prob: float` in `[0, 1]` (fair YES probability before
  calibration), `costs: CostInputs`, `notional_hint: Decimal` (`> 0`, for gas
  amortization), `tick_size: Decimal` (`> 0`, default `0.01`). No `side`: the gate
  derives the side to trade from `sign(edge)`.
- `RiskLimits` — `bankroll: Decimal` (`> 0`), `kelly_fraction: float` in `(0, 1]`,
  `max_position_fraction: float` in `(0, 1]`, `max_position_usd: Decimal` (`> 0`).
- `GateResult` — tagged union via `action: Literal["act", "abstain"]`:
  `ACT(side, edge: float)` or `ABSTAIN(reason: str)`.
- `SizingResult` — `stake_usd: Decimal`, `shares: Decimal`, `binding_cap: str | None`
  (which cap bound, or `None` if Kelly was binding).
- `Decision` — composed output: the `GateResult` plus `SizingResult`
  (ABSTAIN ⇒ zero stake, zero shares).
- `Fill` / `Position` — `entry_price`, `exit_price`, `shares`, `side`, and
  realized `costs` — inputs to P&L accounting tests.
- `CalibrationSample` — `raw_prob: float` in `[0, 1]`, `outcome: int` in `{0, 1}`.

### 2. `core/cost_model.py`

- `round_trip_cost(costs: CostInputs, notional: Decimal) -> float` — per-share
  price hurdle the edge must clear: full `spread` + round-trip fees
  (`2 · fee_rate`) + amortized gas (`gas_usd / notional`, where `gas_usd` is the
  round-trip total) + `model_error_margin`, expressed in price units. Pure;
  **non-negative** and **non-decreasing** in every cost input.

### 3. `core/edge_gate.py`

- `decide(candidate: TradeCandidate, q: float, hurdle: float) -> GateResult` —
  `edge = q − float(candidate.price)`; **ACT iff `abs(edge) ≥ hurdle`**, with
  direction from `sign(edge)`; otherwise `ABSTAIN`. Abstain-by-default. The
  cost-gate law lives **here and nowhere else**.

### 4. `core/sizing.py`

`TradeCandidate.price` is the YES-token price `p` and `q` the fair YES
probability, so the bought token and its Kelly differ by side (exact, not an
approximation):

- `kelly_fraction(q: float, p: float, side: Side) -> float`:
  - `BUY_YES` (buy YES at `p`, fair `q`): `(q − p)/(1 − p)`.
  - `BUY_NO` (buy NO at `1 − p`, fair `1 − q`): `(p − q)/p`.
  - Clamped to `[0, 1]` (non-positive edge ⇒ 0).
- `size(candidate: TradeCandidate, gate: GateResult, limits: RiskLimits) -> SizingResult`
  — needs `candidate` for `p` and `gate` for `side`/`edge`. Fractional Kelly
  (`kelly_fraction · limits.kelly_fraction · bankroll`) then clamped by the three
  hard caps: `max_position_fraction · bankroll`, `max_position_usd`, and
  `bankroll`. Records `binding_cap`. `ABSTAIN ⇒ stake 0, shares 0`. Shares =
  `stake_usd / entry_price`, where `entry_price` is `p` for `BUY_YES` and `1 − p`
  for `BUY_NO`, as `Decimal`.

### 5. `core/calibration.py`

- `Calibrator` — `Protocol` with `fit(samples: Sequence[CalibrationSample]) -> None`
  and `predict(raw: float) -> float`.
- `IsotonicCalibrator` — wraps sklearn `IsotonicRegression(out_of_bounds="clip")`.
- `PlattCalibrator` — logistic (Platt) fit.
- Both: `predict` on an unfitted calibrator raises; output clamped to `[0, 1]`;
  monotonic non-decreasing in `raw`.

### 6. `core/metrics.py`

- `brier_score(probs, outcomes) -> float`.
- `calibration_curve(probs, outcomes, bins) -> list[tuple[float, float, int]]`
  — per bin `(mean_pred, mean_obs, count)`.
- `realized_pnl(fills: Sequence[Fill]) -> Decimal`.
- `roi(pnl: Decimal, deployed: Decimal) -> float`.

### 7. `core/decision.py` — pure compose

- `evaluate(candidate: TradeCandidate, calibrator: Calibrator, limits: RiskLimits) -> Decision`
  — calibrate `raw_prob → q`; `hurdle = round_trip_cost(...)`; `gate = decide(...)`;
  `sizing = size(gate, limits)`; assemble `Decision`. Pure, network-free.

## Data flow (one candidate)

1. `TradeCandidate` arrives: `price` (`Decimal`, on tick), `raw_prob` (`float`).
2. `calibration` maps `raw_prob → q` (fit offline on history; synthetic in tests).
3. `edge = q − float(price)`; `cost_model.round_trip_cost` returns the hurdle.
4. `edge_gate.decide`: ACT iff `abs(edge) ≥ hurdle`, direction `sign(edge)`; else
   ABSTAIN.
5. `sizing.size`: fractional Kelly (`(q−p)/(1−p)` for YES, `(p−q)/p` for NO),
   clamped by the caps; ABSTAIN ⇒ 0.
6. `metrics` score calibration and account P&L/ROI from fills (consumed by the
   backtest in Phase 2; tested here on synthetic fills).

## Test plan (the Phase 1 gate)

Each item maps to a PLAN.md invariant. Property tests use `hypothesis`. TDD: each
component is written red → green with its tests before the next.

- **Cost gate is law** (property, `edge_gate`): ∀ candidate / `q` / hurdle,
  `abs(edge) < hurdle ⇒ ABSTAIN`; `ACT ⇒ abs(edge) ≥ hurdle`.
- **Size within caps** (property, `sizing`): ∀ inputs,
  `stake ≤ min(kelly_stake, max_position_fraction·bankroll, max_position_usd, bankroll)`,
  `stake ≥ 0`, and `ABSTAIN ⇒ 0`.
- **Calibration monotonic + range** (property, `calibration`):
  `raw1 ≤ raw2 ⇒ predict(raw1) ≤ predict(raw2)`; output ∈ `[0, 1]`.
- **Calibration reduces Brier** (deterministic, `calibration`): on a constructed
  overconfident sample (fixed seed, large N), `Brier(calibrated) ≤ Brier(raw)`.
  Deterministic construction rather than random draws to avoid flaky failures.
- **Cost monotonicity** (property, `cost_model`): `round_trip_cost` non-decreasing
  in each cost input and `≥ 0`.
- **P&L balances** (property, `metrics`): `bankroll_end == bankroll_start +
  realized_pnl`; a round-trip at the same price ⇒ `pnl == −costs`.
- **Tick alignment / no float drift** (unit, `models`): off-tick `Price` rejected;
  `Decimal` money arithmetic exact.
- **Pipeline composition** (unit, `decision`): ABSTAIN ⇒ zero-stake `Decision`;
  a known above-hurdle candidate ⇒ ACT with a capped, non-zero stake.

## Implementation ordering

1. Add `numpy`, `scikit-learn` to `pyproject.toml`; `uv lock`; `uv sync`.
2. `core/models.py` (+ tests) — types and validation first; everything depends on
   them.
3. `core/cost_model.py` (+ property tests).
4. `core/edge_gate.py` (+ property tests) — the cost-gate law.
5. `core/sizing.py` (+ property tests) — Kelly + caps.
6. `core/calibration.py` (+ property + Brier-reduction tests).
7. `core/metrics.py` (+ property tests) — Brier, curve, P&L, ROI.
8. `core/decision.py` (+ composition tests) — the pure pipeline.

Each step is red → green plus `ruff` and `mypy` clean before the next. Any
dependency change is followed by `uv lock` before commit, or CI `--frozen` fails.

## Acceptance gate

Inside `nix develop`:

- `uv run ruff check` clean; `uv run ruff format --check` clean.
- `uv run mypy` clean (strict).
- `uv run pytest --cov` green, including every property test above.
- CI encodes the same gates and is green.

Matches `PLAN.md` Phase 1: "unit + property tests for each component. Key
invariants proven: the gate never trades below cost; size never exceeds caps or
bankroll; calibration is monotonic and reduces Brier on overconfident inputs;
P&L accounting balances."

## Out of scope (Phase 1)

- Any network or IO: data adapters, reference-price feed, CLOB/LLM clients
  (Phase 2+).
- Backtest/walk-forward harness and look-ahead guard (Phase 2).
- Signal producers — cross-market consistency, lag/divergence, LLM hypothesis
  generation (Phase 3).
- Live assembly / asyncio orchestration (Phase 4); execution, risk caps, kill
  switch (Phase 5).
- Polymarket market-structure models (added when their consumers exist).
- Sizing to a price-convergence horizon (needs a horizon model; deferred).
