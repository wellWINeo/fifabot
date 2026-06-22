# Phase 4 — Assembly & paper trading (design)

Date: 2026-06-21
Status: approved (design); implementation pending

## Context

`PLAN.md` Phase 4 assembles the tested parts into one pipeline running
end-to-end with simulated fills and **zero real orders**. Phases 0–3 are
complete: a property-tested pure core (`core/`), offline Gamma/CLOB adapters plus
a `ReferencePrice` Protocol with a fixture-backed `ReplayReference` (`data/`), a
look-ahead-safe replay / walk-forward harness exposing a `Strategy` Protocol seam
(`backtest/`), two signal producers (`DivergenceStrategy` S1, `ConsistencyStrategy`
S2) wired through the Phase 1 `evaluate()` pipeline, and a typed-only LLM output
contract (`llm/schema.py`, `HypothesisOutput`).

**Goal (PLAN.md):**

- Strategy wiring: signals → calibration → gate → sizing → **simulated fills**.
- The `pydantic-ai` hypothesis generator + feature extractor (the agent behind
  the Phase 3 output contract).
- Paper-trading mode over **both historical and live data feeds**.

**Gate (PLAN.md):** an end-to-end integration scenario (a worked match with a
known lag event) produces the expected decisions; matches without edge produce
ABSTAIN; the LLM agent runs behind a mock, its output is schema-validated, and
malformed responses never crash the loop.

This spec records the shape so the implementation plan executes it without
re-deciding.

## Decisions

Settled with the user during brainstorming. Each records the decisive reason, not
just the choice.

1. **LLM agent — silent S3 infrastructure (1A).** Build the `pydantic-ai`
   hypothesis/feature agent behind a mock, schema-validate its output, make
   malformed output abstain rather than crash, and run its `p_fair` through
   calibration like any other signal — but keep it **unpromoted**:
   ABSTAIN-by-default for *acting*, no live model call unless a key is set.
   CLAUDE.md: S3 is "lowest priority, hardest path… stays silent" until
   walk-forward-validated. Promoting it now (Option B) would breach the
   overfitting guard; omitting the agent (Option C) fails the gate.

2. **Multi-signal composition — priority precedence S2 > S1 > S3 (2A).** First
   non-abstain signal by CLAUDE.md edge-source priority (cross-market arb > lag /
   divergence > forecast) wins; abstain if all abstain. The decisive reason is
   *type*, not simplicity: **S2 is an accounting identity, not a probability, and
   must never be averaged with a fuzzy forecast.** An ensemble (B) introduces
   unvalidated blend weights — a fresh overfitting surface; independent positions
   (C) permit opposing trades on one market.

3. **Simulated fills — maker-first, fill-if-crossed (3A).** A resting limit at
   our price fills **only if a subsequent in-window quote trades through it**,
   else it expires unfilled; an optional taker fallback may cross the spread.
   Fee + gas are charged per simulated fill. Paper P&L that assumes every order
   fills is actively misleading as a process metric, and "an order may never
   fill" is the behavioral consequence of the maker-first rule, so the simulator
   must represent it. **Sanctioned descope:** if the fill-window / expiry work
   threatens the phase, fall back to a cost-aware taker fill (always fills at the
   crossed price + costs) — **not** to frictionless.

4. **Live feed — adapter + mocked orchestrator (4A).** Build the odds-api.io
   `ReferencePrice` adapter and wire live Gamma/CLOB feeds into an `app/` paper
   loop, unit-tested against injected mocks / recorded fixtures; **live polling is
   human-run** like the existing probe scripts, and **no test touches the
   network**. Graduate `scripts/verify_odds_api.py` / `scripts/probe_odds_detail.py`
   into `scripts/record_reference_fixtures.py`, which also produces the recorded
   reference history that decision 6's train-window calibration consumes. A
   durable live daemon (C) is Phase 5/6 ops — scope creep before real execution
   exists.

5. **Signal promotion / multiple-testing — defer, surface metrics now (5A).**
   Phase 4 reports per-signal Brier + calibration + post-cost EV across
   walk-forward splits (including S3's shadow decisions); it does **not**
   implement a formal promotion/retirement gate. Building a correction now means
   tuning it to current fixtures — meta-overfitting. The formal gate waits for
   accumulated out-of-sample results.

6. **Calibration in walk-forward — per-split, train-window only (+ look-ahead
   test).** Not a real choice: a calibrator is a fitted transform, so fitting it
   on test-window outcomes is look-ahead. Fit **one calibrator per split** on
   train-window `(p_fair, resolved_outcome)` pairs, inject it into the
   test-window strategies, and add an explicit test asserting a calibrator that
   touches test-window outcomes is unreachable.

7. **Orchestrator — thin async orchestrator + small feed abstraction (7A).** All
   financial logic stays in `core/`; `app/` is wiring. The one new seam is a
   **feed interface yielding timestamped events**, implemented by both a
   historical iterator and a live async stream — the seam that makes decisions 3,
   4, and 7 fall out cleanly. Extending the Phase 2 engine (B) would conflate
   deterministic offline backtest with async/network live and pollute a clean
   module.

### Cross-cutting refinements (decided during review)

- **S3 runs in shadow mode (ties 1, 2, 5).** Because S3 is unpromoted it never
  wins precedence in composition — but it still produces a **calibrated
  `Decision` that is recorded, not traded**, generating exactly the walk-forward
  Brier / EV evidence a future promotion gate (5) will consume. This is an
  **explicit flag** — *unpromoted → may log a decision, may not open a
  position* — not implicit behavior. So 1A's apparent con ("the LLM is just
  plumbing") inverts: the plumbing's output is the phase's most valuable
  research artifact.

- **The look-ahead guard distinguishes decision-time from fill-window (3 ↔ 6).**
  A strategy's decision at time `t` may read only data with ts ≤ `t` (MarketView
  already enforces this). But the fill simulator for an order resting from `t`
  **legitimately** consumes quotes in `(t, t+expiry]` to decide whether it
  filled — that is forward simulation of an order's life, **not** look-ahead. The
  guard must assert (a) no decision reads data past its own timestamp and (b) no
  calibrator touches test-window outcomes, **without** flagging the fill
  simulator's forward window. A blunt timestamp guard would false-positive on the
  maker-fill model; the fill simulator therefore receives its forward quotes by a
  path that does **not** go through the guarded `MarketView`.

- **Log signal agreement without acting on it (preserves the ensemble's only real
  upside).** When two signals fire the same direction on a market, record it as
  metadata on the decision log. This keeps "do aligned signals predict better?"
  answerable later without introducing unvalidated ensemble weights now.

- **Numeric representation carries over.** `Decimal` for prices / quotes / cash;
  `float` for the statistical / decision math (`p_fair`, edges, Brier). Reference
  odds arrive as strings → parsed to `Decimal` at the boundary, de-vigged to
  `float` fair via the existing `core/signals/devig.py`.

## Components

### core/ — pure additions (no IO, no `data` / `backtest` / `app` imports)

#### 1. `core/fills.py` — the maker-first fill model
- `MakerOrder` (frozen): `side: Side`, `limit_price: Decimal` (the YES-token
  price we rest at — the decision-time quote price), `shares: Decimal`,
  `placed_ts`, `expiry_ts`.
- `crosses(order, quote) -> bool` — true iff `placed_ts < quote.ts ≤ expiry_ts`
  and the quote's token price trades through the limit
  (`token_price(side, quote.price) ≤ token_price(side, limit_price)`: BUY_YES
  fills when the YES price reaches ≤ limit; BUY_NO is symmetric on `1 − price`).
  This single predicate is shared by the engine and the orchestrator.
- `simulate_maker_fill(order, future_quotes: Sequence[Quote]) -> datetime | None`
  — **pure**. Returns the ts of the first crossing quote (the entry executes at
  the order's limit token price), else `None` (expired, no position). The
  optional taker fallback is pinned in the plan's tests.
- `round_trip_fill_costs(costs, entry_price, exit_price, shares) -> Decimal`
  — `fee_rate*(entry+exit)*shares + gas_usd`. The engine / orchestrator build the
  round-trip `Fill` at **close** time and set `Fill.costs_usd` from this; the fill
  model itself returns only the entry timing, keeping it free of close-side state.
- This is the genuinely-shared, genuinely-financial piece; **both** the backtest
  engine and the live orchestrator consume it. (`Fill.costs_usd`, currently
  always `0`, becomes meaningful.)

#### 2. `core/signals/base.py` — promotion flag (extend, do not rewrite)
- Add an explicit notion of **promotion** so "unpromoted → log, don't act" is a
  declared property, not a special case in the composer. The flag lives on the
  **strategy** (`promoted: bool`); the composer reads `strategy.promoted` to
  decide whether a non-abstain decision may act, and the decision log records it
  per signal.

### data/ — live reference adapter (IO at the edge)

#### 3. `data/oddsapi.py` — odds-api.io reference adapter
- Pure parsers: `/odds` ML payload → per-outcome decimal odds → `1/odds` →
  `core/signals/devig.devig` → fair `p` per outcome, with `overround` and the
  per-market `updatedAt` freshness. Thin liquidity (per-outcome depth) → abstain
  signal, surfaced so S1 can skip a thin reference.
- Async `OddsApiClient` (httpx, `ODDS_API_KEY` from env only) for human-run live
  polling — **never reached by a test**.
- A `RecordedReference` `ReferencePrice` impl (following the `ReplayReference`
  as-of pattern) that replays self-recorded timestamped snapshots, so S1's live
  path and its backtest path share one interface.
- **Identity mapping is explicit config**, not auto-matched: a human-curated map
  `{polymarket_market_id → (oddsapi_event_id, outcome)}`. Live polling is
  human-run, so curation is acceptable and avoids a fragile fuzzy-match. Edge
  cases from the probe (settled events → empty `bookmakers`; "Bet365 (no
  latency)" variant to filter; request only `ML`) are handled in the parser.

#### 4. Live Gamma group fetching (compose existing client calls)
- `GammaClient` already fetches markets and `parse_event_groups` already builds
  `MarketGroup`s from a negRisk event. Phase 4 adds the thin async glue that
  fetches live events and yields the `MarketGroup`s the live S2 path needs.
  Tested against recorded fixtures (the existing `events_negrisk.json` shape).

### app/ — the thin async orchestrator (wiring only; may import `core` + `data` + `backtest`)

#### 5. `app/feed.py` — the feed seam
- `Feed` Protocol: `events() -> AsyncIterator[MarketEvent]` — one timestamped
  event source the orchestrator consumes, regardless of origin.
- `HistoricalFeed(events: Sequence[MarketEvent])` — async-yields pre-loaded,
  chronologically-validated events (reuses `backtest.feed.load_events` for the
  ordering guard). Deterministic; this is the "paper trade over historical data"
  mode of the assembled pipeline. (Walk-forward **metric** runs stay on the
  synchronous `backtest/` engine + harness — the orchestrator demonstrates the
  end-to-end pipeline; both share the one fill model.)
- `LiveFeed(clob_ws, market_ids)` — async-yields events off `ClobWsClient.stream`,
  converting `Quote → MarketEvent` (`event_from_quote`). Live only; mocked in
  tests via an injected async iterator.

#### 6. `app/orchestrator.py` — the paper-trading run loop
- A `PaperTrader` that consumes a `Feed`, builds the as-of `MarketView` per event
  (the decision-time look-ahead guard), runs the **composite** strategy →
  `evaluate()` → places a `MakerOrder` → resolves it via `simulate_maker_fill`
  over subsequent in-window quotes → accounts P&L (`core.metrics.realized_pnl`).
  **Zero real orders** — fills are simulated only.
- Drains the **signal decision log** (per-signal `source`, `p_fair`, calibrated
  decision, `promoted`, agreement flag) for the metrics layer. Only promoted
  signals can produce an acting decision; shadow (S3) decisions are logged only.
- Same loop, two feeds: `HistoricalFeed` → deterministic paper backtest;
  `LiveFeed` → human-run live smoke test.

### backtest/ — composition + engine fill upgrade

#### 7. `CompositeStrategy` (in `backtest/signals.py`)
- Holds the sub-strategies in priority order (S2, S1, S3-shadow). `on_event`
  returns the **acting** `Decision | None` — the first *promoted* sub-strategy
  that does not abstain; abstain if all abstain. Appends every sub-signal's
  `(source, p_fair, decision, promoted)` plus a same-direction **agreement** flag
  to an injected decision-log collector.

#### 8. `backtest/engine.py` — adopt the maker fill model
- Replace the inline frictionless fill with `core.fills.simulate_maker_fill`, so
  walk-forward post-cost EV is meaningful. The engine stays synchronous,
  deterministic, offline. The fill simulator reads forward quotes **directly**
  (not via `MarketView`), per the decision-time / fill-window distinction.

#### 9. `backtest/report.py` — per-signal metrics (extend)
- Aggregate per-signal **Brier + calibration curve + post-cost EV** across splits
  from the decision log + resolved outcomes (reuses `core.metrics`). Include S3
  shadow decisions and the agreement metric. **No** promotion gate. This is the
  evidence a future Phase 4+/5 promotion rule will consume.

### llm/ — the shadow S3 agent

#### 10. `llm/agent.py` — `pydantic-ai` hypothesis generator + feature extractor
- A `HypothesisAgent` wrapping `pydantic-ai`, **model-agnostic**, returning the
  existing `HypothesisOutput`. The model is **injectable** so tests run a mock
  (no live model, no network). Malformed / invalid model output is caught at the
  boundary and yields an **abstain**, never an exception that reaches the loop.
- Feature extraction into typed structures (the inputs handed to the agent) lives
  here too, kept minimal.
- New runtime dependency: **`pydantic-ai`** (this phase's only dependency
  addition; `uv.lock` updated once).

#### 11. `ShadowForecastStrategy` (S3, in `backtest/signals.py`)
- Wraps `HypothesisAgent`: gather features from the as-of `MarketView` → agent →
  `HypothesisOutput.p_fair` → calibrate → `evaluate()` → `Decision`, marked
  **unpromoted** (logged, never acts). Malformed agent output → abstain.

### scripts/ — fixture recorder

#### 12. `scripts/record_reference_fixtures.py`
- Graduates the two throwaway probes into a tool that polls odds-api `/odds` (ML,
  Betfair Exchange + Bet365) and persists **timestamped snapshots** that
  `RecordedReference` replays. This self-records Betfair-Exchange history (the
  probe found `/odds/movements` covers only Bet365). Human-run; not in the suite.

## Data flow (one paper-traded event)

1. A `Feed` yields a `MarketEvent` (historical iterator or live WS stream).
2. The orchestrator builds the as-of `MarketView` (decision-time guard: data
   ≤ event ts only).
3. `CompositeStrategy.on_event`: S2 (de-vig sibling legs) → S1 (`reference_at` →
   divergence) → S3-shadow (agent → hypothesis), each calibrated and gated via
   `evaluate()`. First **promoted** non-abstain wins; all sub-decisions +
   agreement flag are logged.
4. On an acting decision, a `MakerOrder` rests at the decided price; the engine /
   orchestrator feeds it subsequent in-window quotes; `simulate_maker_fill`
   returns a `Fill` (at limit, + fee/gas) or `None` (expired, no position).
5. P&L is accounted on fills; the decision log + resolved outcomes feed the
   per-signal Brier / calibration / post-cost EV report.

## Test plan (the Phase 4 gate)

Property tests use `hypothesis`. TDD: each component red → green before the next.
No test touches the network (the autouse `conftest` guard stays in force); the
LLM model and the live clients are mocked / injected.

- **Fill model** (unit/property): an order fills at its limit when an in-window
  quote crosses, and only then; expires (`None`) with no crossing quote; fee/gas
  applied; BUY_YES and BUY_NO symmetric; empty window → no fill.
- **Composite precedence** (unit): S2 beats S1 beats S3 when several would act;
  abstain when all abstain; a **shadow S3 that would act never opens a position
  but is logged**; agreement flag set when two signals fire the same direction.
- **Orchestrator** (unit, async): over a `HistoricalFeed` of a synthetic match
  the loop produces fills and a populated decision log; over a mocked `LiveFeed`
  (injected async iterator) it runs identically with **zero real orders**.
- **Per-split calibration look-ahead** (unit): the walk-forward composition fits
  the calibrator **only** on train-window samples; a deliberate attempt to fit on
  test-window outcomes is unreachable / rejected. The fill simulator's forward
  window is **not** flagged by the decision-time guard.
- **odds-api adapter** (unit): recorded `/odds` ML fixture → de-vigged fair `p`
  per outcome + overround + freshness; thin per-outcome depth → abstain; settled
  event (empty `bookmakers`) handled; identity map drives `market_id → outcome`.
- **LLM agent** (unit): a mocked model yields a valid `HypothesisOutput`;
  malformed model output is rejected at the boundary and produces an **abstain**,
  never an exception reaching the loop. (Satisfies the moved Phase 3 clause.)
- **End-to-end integration scenario** (the gate): a worked match with a known lag
  event (Polymarket trailing the reference) produces the expected **ACT** with
  the right side/size; a no-edge match produces **ABSTAIN**; deterministic on
  repeat.
- **Report** (unit): per-signal Brier / calibration / post-cost EV aggregate
  sanely across splits, including S3 shadow decisions and the agreement metric.

## Implementation ordering

Per the agreed build order; each step red → green plus `ruff` and `mypy` clean
before the next.

1. **Feed abstraction** (`app/feed.py`) — `Feed` Protocol, `HistoricalFeed`,
   `LiveFeed` (+ tests with an injected async iterator).
2. **Fill model** (`core/fills.py`) — `MakerOrder`, `simulate_maker_fill`
   (+ unit/property tests); then upgrade `backtest/engine.py` to use it.
3. **Thin orchestrator** (`app/orchestrator.py`) — `PaperTrader` over a `Feed`,
   decision log drain, simulated fills (+ async tests, zero real orders).
4. **odds-api adapter + recorder** (`data/oddsapi.py`, `RecordedReference`,
   `scripts/record_reference_fixtures.py`, recorded `/odds` fixture) + live Gamma
   group glue (+ tests).
5. **S3 shadow agent** (`llm/agent.py` + `ShadowForecastStrategy`,
   `promoted` flag in `core/signals/base.py`, add `pydantic-ai`, `uv lock`)
   (+ mocked-model + malformed-response tests).
6. **Composite + per-split calibration** (`CompositeStrategy`, walk-forward
   wiring that fits one calibrator per split on train-window pairs) + the
   look-ahead test distinguishing decision-time from fill-window.
7. **Metrics surfacing** (`backtest/report.py`) — per-signal Brier / calibration
   / post-cost EV + agreement, including S3 shadow.
8. **End-to-end integration scenario** — the worked lag-event match + the no-edge
   ABSTAIN match.
9. Update `PLAN.md`: mark Phase 4 progress.

One dependency addition (`pydantic-ai`) → a single `uv lock` step at step 5.

## Acceptance gate

Inside `nix develop`:

- `uv run ruff check` clean; `uv run ruff format --check` clean.
- `uv run mypy` clean (strict).
- `uv run pytest --cov` green, including every test above, with **no live
  network** reachable from any test.
- CI encodes the same gates and is green.

Matches `PLAN.md` Phase 4: the end-to-end lag-event scenario produces the
expected decisions; no-edge matches ABSTAIN; the LLM agent runs behind a mock,
its output is schema-validated, and malformed responses never crash the loop.

## Out of scope (Phase 4)

- Real order placement / signing, allowance handling, maker-first **execution**
  on Amoy testnet, risk caps + kill switch (Phase 5).
- A formal signal-promotion / multiple-testing-corrected gate; Phase 4 only
  **surfaces** the per-signal evidence (Phase 4+/5).
- Dedicated near-riskless **basket gate** + atomic multi-leg execution (Phase 5).
- A durable long-running live daemon (persistence, reconnect/backoff, state
  recovery) (Phase 5/6 ops).
- News / lineup ingestion for a richer S3 (later).
- Live real-money trading (Phase 6).
