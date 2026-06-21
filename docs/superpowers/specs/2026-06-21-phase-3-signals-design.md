# Phase 3 — Signals (design)

Date: 2026-06-21
Status: approved (design); implementation pending

## Context

`PLAN.md` Phase 3 turns the three edge sources into testable producers feeding
the Phase 1 domain core. Phases 0–2 are complete: a property-tested pure core
(`core/`), offline Gamma/CLOB adapters plus a `ReferencePrice` Protocol with a
fixture-backed `ReplayReference` (`data/`), and a look-ahead-safe replay /
walk-forward harness exposing a `Strategy` Protocol seam (`backtest/`).

**Goal:** two working signal producers — **S1 (lag/divergence vs. a sharp
reference)** and **S2 (cross-market consistency / arbitrage)** — wired into the
existing harness through the Phase 1 `evaluate()` pipeline, plus a **typed LLM
output contract** (pydantic models only, no agent). Each signal flags known
synthetic mispricings and abstains otherwise, with no real network in tests.

This spec records the shape so the implementation plan executes it without
re-deciding.

## Decisions

Settled with the user during brainstorming:

- **S2 contract — unified `p_fair`, detection-only (Approach A).** All signals
  emit the same shape the core already consumes: a per-market no-vig fair YES
  probability (`raw_prob`), or abstain. S2 de-vigs a mutually-exclusive group
  (normalize the leg prices so they sum to 1.0) and emits each leg's fair `p`,
  so the existing single-market gate/sizing handle it unchanged. This
  **understates** the true riskless-basket edge (per-leg gating applies a
  model-error margin that an accounting-identity arbitrage should not pay) — a
  dedicated near-riskless **basket gate + atomic multi-leg execution is a known
  Phase 5 item**, recorded here so the insight is not lost. S2 still records the
  `group_id` and the `overround` observed at signal time on its output so Phase 5
  can reconstruct the basket; it does not act on them in Phase 3.
- **S1 reference — bind to the `ReferencePrice` Protocol; concrete adapter is
  Phase 4.** S1 depends only on the existing Protocol and is tested against
  `ReplayReference` fixtures. The live reference adapter is IO at the edge with
  no live consumer until Phase 4 paper trading, so it is deferred there. The
  lead concrete source is **odds-api.io** (validated during brainstorming, see
  below); the choice stays swappable because S1 never names a vendor.
- **LLM scope — typed contract only (Option B).** Phase 3 defines the LLM
  layer's typed output models and validates them; it does **not** build the
  `pydantic-ai` agent and does **not** add the dependency. The agent, mocked-
  model tests, and malformed-response robustness move to Phase 4. **This
  re-scopes `PLAN.md`:** the Phase 3 gate clause "LLM output is schema-validated,
  mocked in tests, and malformed responses never crash the loop" shifts to
  Phase 4 (schema validation alone remains in Phase 3). `PLAN.md` is updated to
  reflect this.
- **No new runtime dependency in Phase 3.** Deferring `pydantic-ai` means no
  dependency addition and no `uv.lock` churn this phase.

Additional decisions made in the design:

- **Pure core, thin edges — the two-layer split.** `core/` must not import
  `data`/`backtest`. So signal *math* lives as pure functions in `core/signals/`
  (operating on primitive prices, unit/property-tested in isolation), and the
  *harness wiring* that reads a `MarketView` and emits a `Decision` lives in
  `backtest/` as thin `Strategy` implementations. Multi-signal composition and
  paper trading are Phase 4 (`app/`).
- **One shared de-vig primitive.** `core/signals/devig.py` normalizes a set of
  values that should sum to 1.0. Polymarket YES legs pass their prices directly;
  decimal book odds pass `1/odds`. Used by S2 now and by the Phase 4 odds-api
  reference adapter, so de-vig logic exists once and is tested once.
- **Gating stays single-sourced.** Signals are dumb producers of `p_fair` plus
  *structural* abstains (no reference, incomplete group, reference too thin).
  Whether an edge is large enough remains the **cost gate's** job
  (`CLAUDE.md`: "cost gate is law"): when `p_fair ≈ price`, edge < hurdle →
  abstain automatically. No signal re-implements the hurdle, so "abstain within
  noise" is proven by reusing the Phase 1 gate, not by duplicated thresholds.
- **`MarketGroup` enters now, keyed on Gamma `negRisk`.** S2 is intrinsically
  group-level, and Phase 2 explicitly deferred linked-market grouping to Phase 3.
  Gamma was probed live (public, no-auth — `scripts/probe_gamma.py`) and the
  shape confirmed: an event nests a `markets` array and carries an
  `enableNegRisk`/`negRisk` flag; **`negRisk=True` marks mutually-exclusive legs
  whose YES prices should sum to ~1.0** — exactly S2's target (e.g. event 30615
  "World Cup Winner", negRisk, 60 country legs). So Phase 3 adds the
  `MarketGroup` model and a pure `parse_event_groups` keyed off the negRisk flag,
  tested against a **recorded `/events` fixture** (real shape, not invented).
  Live group *fetching* over the network is Phase 4. The unifying concept is "a
  negRisk event's legs," which covers both a 3-way match and an N-way winner.
- **Numeric representation carries over.** `Decimal` for prices/quotes/cash;
  `float` for the statistical/decision math (`p_fair`, edges, Brier). Reference
  odds arrive as strings and are parsed to `Decimal` at the boundary.

## Reference source — odds-api.io (validated, Phase 4 consumer)

Probed live during brainstorming (`scripts/verify_odds_api.py`,
`scripts/probe_odds_detail.py`) to de-risk the S1 reference before committing:

- **Free plan:** 2 selectable bookmakers, **100 requests/hour** (confirmed via
  `x-ratelimit-limit` header), REST. Paid tiers (£99+/mo) are out of budget, so
  Phase 4 operates strictly on the free tier.
- **Bookmakers selected:** **Betfair Exchange** (primary) + **Bet365**
  (cross-check / history). Pinnacle and all non-Betfair exchanges are absent
  from the provider.
- **Betfair Exchange ML** returns **back + lay prices and per-outcome depth**
  plus a per-market `updatedAt` — giving a back/lay mid (fair price), a
  liquidity gauge (thin book → abstain), and a freshness timestamp. Observed
  overround ≈ **0.55%** vs Bet365's ≈ **4.76%**, confirming the exchange is the
  sharper reference.
- **World Cup coverage:** league slug `international-fifa-world-cup` (68 events);
  1X2 market is named `ML` with `home`/`draw`/`away`, de-vigs cleanly.
- **History:** `/odds/movements` is on the free plan but returns data **only for
  Bet365**, not Betfair Exchange. So for backtests: Bet365 series are available
  immediately; **Betfair Exchange history must be self-recorded** going forward
  by polling `/odds` and persisting timestamped snapshots (which `ReplayReference`
  replays). Per-outcome depth varies (home liquid, draw/away thin) → trust is
  per-outcome.
- **Edge cases noted for the Phase 4 adapter:** settled events return empty
  `bookmakers: {}`; `/odds/updated` needs a sport *id* (not the `football`
  slug); selecting "Bet365" also surfaces a "Bet365 (no latency)" variant to
  filter out; the full `/odds` payload is large (~260 KB) so request only `ML`.

None of this blocks Phase 3 — S1 is tested against `ReplayReference` fixtures,
which can be self-recorded from these two free bookmakers. The exploratory probe
scripts graduate into a proper `scripts/record_reference_fixtures.py` in Phase 4.

## Components

### core/signals/ — pure signal math (no IO, no `data`/`backtest` imports)

#### 1. `core/signals/devig.py`
- `overround(values: Sequence[float]) -> float` — the sum that should be ~1.0.
- `devig(values: Sequence[float]) -> list[float]` — normalize to sum 1.0;
  rejects empty/non-positive input. Callers pass YES prices (Polymarket) or
  `1/odds` (decimal book odds).

#### 2. `core/signals/consistency.py` (S2)
- `scan_consistency(yes_prices: Sequence[Decimal]) -> ConsistencyResult` with
  `overround` and `fair_legs: list[float]` (the de-vigged per-leg fair). Pure.

#### 3. `core/signals/divergence.py` (S1)
- `divergence(pm_yes: Decimal, ref_fair: float) -> DivergenceResult` with
  `fair = ref_fair` and the raw signed divergence. Pure. Threshold/“noise” is
  *not* applied here — the cost gate downstream decides actionability.

#### 4. `core/signals/base.py`
- `SignalOutput` (frozen pydantic): `p_fair: float`, `source: str`,
  `group_id: str | None`, `overround: float | None`, `rationale: str`.
- A `Signal` Protocol is **not** added to `core/` (its `MarketEvent`/`MarketView`
  inputs are IO types). The harness seam stays the Phase 2 `Strategy` Protocol.

### data/events.py — `MarketGroup`
- `MarketGroup(group_id: str, market_ids: tuple[str, ...], kind: str)` (frozen);
  `kind` e.g. `"negrisk"`. A group is the set of mutually-exclusive YES legs of
  one Gamma negRisk event.
- `parse_event_groups(...)` — pure, tested against a **recorded** Gamma `/events`
  fixture (real shape verified by probe). Selects events where
  `enableNegRisk`/`negRisk` is true and emits one `MarketGroup` per event from
  its nested `markets`. Parsing notes captured from the probe: event `id`/`ticker`
  → `group_id`; each nested market's `clobTokenIds`/`outcomes` arrive as
  **JSON-encoded strings** (decode once more) and are a `[YES, NO]` token pair;
  `groupItemTitle` is the leg label. Live group *fetching* over the network is
  Phase 4.

### backtest/ — thin harness wiring (may import `core` + `data`)

#### 5. `backtest/signals.py`
- `DivergenceStrategy` and `ConsistencyStrategy`, each implementing the Phase 2
  `Strategy` Protocol (`on_event(event, view) -> Decision | None`). They hold
  config (cost inputs, notional hint, calibrator, limits; for S2 the
  `MarketGroup`s). On each event they gather the needed prices from the as-of
  `MarketView` (S1: `view.reference_at`; S2: sibling-leg `view.latest_price`),
  call the pure function, build a `TradeCandidate(price, raw_prob=p_fair, …)`,
  run `core.decision.evaluate`, and return the `Decision`. Structural abstains
  (no reference / thin reference / incomplete group) return `None`.

#### 6. `backtest/report.py` — Brier/calibration aggregation
- Realize the deferred hook: aggregate **Brier score + calibration curve** over
  signal `p_fair` vs. the realized 0/1 outcome from match resolution (the label
  the existing `core.models.CalibrationSample` already models — not derived from
  a terminal price, which `Quote` cannot represent as exactly 0/1). Requires the
  engine to surface each signal's probability paired with that outcome — a small
  extension to the result/collection path. Reuses `core.metrics.brier_score` /
  `calibration_curve`.

### llm/ — typed contract only (Option B)

#### 7. `llm/schema.py`
- Frozen pydantic models for the deferred layer's typed output (e.g.
  `HypothesisOutput` with `p_fair`, `confidence`, `rationale`). Validated at
  construction; malformed input rejected by pydantic. **No** `pydantic-ai`
  import, **no** agent, **no** dependency.

## Data flow (one S1/S2 evaluation)

1. Harness pushes a `MarketEvent`; builds the as-of `MarketView`.
2. **S2:** look up the event leg's siblings' latest prices in the view →
   `scan_consistency` → de-vigged `p_fair` for this leg (+ `group_id`,
   `overround` recorded for Phase 5).
   **S1:** `view.reference_at(market_id, ts)` (under the as-of guard) → reference
   fair → `divergence` → `p_fair = ref_fair`; abstain if no/thin reference.
3. Wrapper builds `TradeCandidate(price=event YES, raw_prob=p_fair, costs,
   notional)` → `evaluate()` → calibrate → cost gate → size → `Decision`.
4. Engine accounts P&L (Phase 2 deterministic fill model) and records
   `(p_fair, resolved 0/1 outcome)` for Brier/calibration aggregation.

## Test plan (the Phase 3 gate)

Property tests use `hypothesis`. TDD: each component red → green before the next.

- **devig** (property): output sums to 1.0, order-preserving, 2-way & N-way;
  empty/non-positive input rejected.
- **S2 consistency** (unit/property): flags a synthetic over-round basket
  (Σ YES > 1) and abstains when legs sum ≈ 1.0 (via the downstream gate);
  de-vigged legs sum to 1.0; `overround` correct.
- **S1 divergence** (unit): flags when the reference diverges beyond the cost
  hurdle; abstains when aligned, when no reference, or when the reference is too
  thin; **no-look-ahead preserved** — reference queried under the as-of guard,
  plus a deliberate future-peek that must raise `LookAheadError`.
- **MarketGroup parser** (unit): recorded `/events` fixture → groups; only
  negRisk events become groups; JSON-string `clobTokenIds`/`outcomes` decoded;
  malformed payload rejected.
- **Signal strategies in harness** (unit): deterministic replay yields `ACT` on
  a synthetic mispricing and `ABSTAIN` on aligned markets.
- **Report** (unit): Brier/calibration aggregation is sane (well-calibrated
  inputs → low Brier).
- **LLM schema** (unit): valid output validates; malformed dict rejected by
  pydantic. (The only LLM test this phase.)
- **No live network** (all tests): existing autouse guard remains in force.

## Implementation ordering

Each step is red → green plus `ruff` and `mypy` clean before the next.

1. `core/signals/devig.py` (+ tests) — the shared primitive.
2. `core/signals/consistency.py` and `core/signals/divergence.py` (+ tests) —
   pure S2/S1 math and result models; `core/signals/base.py` `SignalOutput`.
3. `data/events.py` `MarketGroup` + `parse_event_groups` (+ tests, recorded
   Gamma `/events` fixture).
4. `backtest/signals.py` (+ tests) — `DivergenceStrategy`,
   `ConsistencyStrategy` over the harness; ACT/ABSTAIN scenarios.
5. `backtest/report.py` Brier/calibration extension (+ tests), with the engine
   change needed to surface signal probabilities.
6. `llm/schema.py` (+ tests) — typed output contract.
7. Update `PLAN.md`: mark Phase 3 progress and move the LLM robustness clause to
   Phase 4.

No dependency changes, so no `uv lock` step this phase.

## Acceptance gate

Inside `nix develop`:

- `uv run ruff check` clean; `uv run ruff format --check` clean.
- `uv run mypy` clean (strict).
- `uv run pytest --cov` green, including every test above, with **no live
  network** reachable from any test.
- CI encodes the same gates and is green.

Matches `PLAN.md` Phase 3 (as re-scoped): "each signal flags known synthetic
mispricings and abstains within noise; LLM output is schema-validated" — with
the agent/mocked/malformed-robustness clause moved to Phase 4.

## Out of scope (Phase 3)

- Live odds-api.io adapter and Betfair-Exchange self-recording (Phase 4).
- `pydantic-ai` agent, the dependency, mocked-model tests, and LLM malformed-
  response robustness (Phase 4).
- Live Gamma linked-group fetching (Phase 4); Phase 3 uses fixtures.
- News / lineup data ingestion for S3 (later).
- Multi-signal composition and paper trading (Phase 4).
- Dedicated near-riskless **basket gate** and atomic multi-leg execution
  (Phase 5).
- Signal promotion / multiple-testing correction over walk-forward (Phase 4+).
- Realistic simulated fills / slippage (Phase 4); real execution, risk caps,
  kill switch (Phase 5).
