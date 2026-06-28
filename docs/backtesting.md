# Backtesting guide

How to run a real walk-forward backtest of the signals against recorded
Polymarket history, and how to read the result. This is the **process
scorecard** that `CLAUDE.md` calls the real measure of success — not live P&L.

> Status: the backtest *harness* is complete and tested (Phase 2–4). The data
> collection, outcome labelling, runner, and promotion gate described below are
> **not built yet**. This guide is both a how-to and the spec for that work.

---

## What already exists

Pure, tested, and ready to wire together:

| Piece | Symbol | File |
|-------|--------|------|
| Leak-free splits | `walk_forward_splits` | `backtest/walkforward.py` |
| As-of view (look-ahead guard) | `MarketView` | `backtest/view.py` |
| Replay engine (maker-first fills) | `replay` | `backtest/engine.py` |
| Per-split calibration walk-forward | `run_walk_forward` | `backtest/calibrated.py` |
| Signal strategies S1/S2/S3 | `DivergenceStrategy`, `ConsistencyStrategy`, `ShadowForecastStrategy`, `CompositeStrategy` | `backtest/signals.py` |
| P&L aggregation | `aggregate` | `backtest/report.py` |
| Per-signal Brier + calibration curve | `per_signal_scores` | `backtest/report.py` |
| Scoring math | `brier_score`, `calibration_curve` | `core/metrics.py` |
| Data adapters | `ClobClient.fetch_price_history`, gamma, `OddsApi` reference | `data/` |
| Quotes ⇄ DataFrame | `quotes_to_frame`, `frame_to_events` | `data/history.py` |

The look-ahead guarantees are already structural and have a test that injects a
future-peek and asserts it is rejected. You do not need to re-prove that.

## What is missing (the work to do before a real run)

1. **Collect & persist history.** No price series are stored on disk (only test
   fixtures). Pull real CLOB price history per token over a date window and
   persist it (parquet via `quotes_to_frame`).
2. **Collect reference snapshots.** S1 (`DivergenceStrategy`) needs a
   `ReferencePrice` over time. Record odds-api ML snapshots (see
   `scripts/record_reference_fixtures.py` for the pattern) and replay them with
   `ReplayReference` (`data/reference.py`).
3. **Resolved outcomes.** `run_walk_forward` and `per_signal_scores` require
   `outcomes: Mapping[str, int]` — final 0/1 per `market_id`. **No producer
   exists.** Fetch resolved Gamma markets and map each YES token to 1 (resolved
   YES) or 0 (resolved NO). Outcomes are post-hoc only — never read during a
   decision (`report.py` enforces this by joining after the fact).
4. **A runner entrypoint** — `scripts/run_backtest.py` — wiring the above into
   `run_walk_forward` + `aggregate` + `per_signal_scores` and printing a
   scorecard. Does not exist.
5. **Thread real costs.** `run_walk_forward` currently replays with
   `_ZERO_COSTS`, so realized P&L understates cost. Add a `costs: CostInputs`
   parameter and pass it to both `replay` calls. (The edge gate is already
   cost-aware via `TradeCandidate.costs`, so abstain decisions are correct; this
   only fixes the realized-P&L accounting.)
6. **Multiple-testing correction.** `promoted` is a manual boolean. The
   `CLAUDE.md` overfitting guard requires promoting a signal only after
   out-of-sample survival with a correction across the signals tested
   (e.g. Benjamini–Hochberg / deflated metric). Not implemented.

## End-to-end flow (once the above exists)

```
collect price history  ─┐
collect ref snapshots  ─┼─►  persisted dataset  ─►  frame_to_events()  ─►  events
fetch resolved markets ─┘                                                    │
                                                                            ▼
                          walk_forward_splits(n, train_size, test_size)  ─►  splits
                                                                            │
        make_strategy(calibrator) = CompositeStrategy([                     │
            NamedSignal("S1", DivergenceStrategy(...calibrator...), promoted=True),
            NamedSignal("S2", ConsistencyStrategy(...calibrator...), promoted=True),
            NamedSignal("S3", ShadowForecastStrategy(...calibrator...), promoted=False),
        ], log=log)                                                         │
                                                                            ▼
        run_walk_forward(events, splits, make_strategy, outcomes,
                         make_calibrator, limits)  ─►  list[BacktestResult]
                                                                            │
                         aggregate(results)        ─►  total/mean P&L, ROI  │
                         per_signal_scores(log, outcomes)  ─►  per-signal Brier + curve
```

Sketch of the runner core (illustrative — wire to your collected dataset):

```python
events = frame_to_events(pl.read_parquet("data/history/<window>.parquet"))
splits = walk_forward_splits(len(events), train_size=..., test_size=...)
log: list[SignalDecision] = []

def make_strategy(cal: Calibrator) -> Strategy:
    return CompositeStrategy(
        [
            NamedSignal("S1", DivergenceStrategy(costs=COSTS, notional_hint=NOTIONAL,
                                                 calibrator=cal, limits=LIMITS), True),
            NamedSignal("S2", ConsistencyStrategy(groups=GROUPS, costs=COSTS,
                                                  notional_hint=NOTIONAL,
                                                  calibrator=cal, limits=LIMITS), True),
        ],
        log=log,
    )

results = run_walk_forward(events, splits, make_strategy, outcomes,
                           make_calibrator=PlattCalibrator, limits=LIMITS)
print(aggregate(results))
print(per_signal_scores(log, outcomes))
```

Reference adapters are async; collection scripts should mirror the existing
`scripts/probe_*.py` / `record_reference_fixtures.py` style (recorded payloads,
no live network in unit tests).

## Reading the scorecard

A signal is worth promoting only if, **out of sample**, it clears all of:

- **Calibration beats the no-vig market.** Its `per_signal_scores` Brier is
  lower than the de-vigged reference baseline over the same markets, and the
  `calibration_curve` tracks the diagonal (predicted ≈ observed per bin).
- **Positive post-cost EV.** `aggregate(...).total_pnl` and `mean_roi` are
  positive *with real `CostInputs` threaded* — not zero-cost.
- **Survives multiple-testing correction.** The edge is not the best of many
  noisy candidates; it holds after the correction across all signals tested.
- **Stable across splits.** `per_split_pnl` is not one lucky window; the edge
  recurs across the walk-forward folds.

Live P&L on ~$25 is statistically meaningless and is only an infra smoke test —
do not read it as evidence either way.

## Parallel track: Phase 6 operational gate

Independent of backtesting, Phase 6's harness (`scripts/live_microtrade.py`) is
built but its gate is **operational**, not code: a clean end-to-end testnet
(Amoy, chain 80002) run, then one monitored micro-trade reconciled
(expected vs. actual fill and slippage) before any further use. That validates
the live path; it is not the scorecard.
