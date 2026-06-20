# PLAN.md

High-level implementation plan. Phases are sequential and TDD-gated: a phase is
done only when its acceptance gate is green (`pytest` + `ruff` + `mypy`). Real
money moves only in the final phase, after every prior gate has passed.

Low-level test design lives in each phase's working notes, not here.

---

## Phase 0 — Foundation

**Goal:** a working repo where "tests after every step" is enforced by machinery,
not discipline.

**Deliverables:** `uv` project, `pytest`/`ruff`/`mypy`/pre-commit, CI that blocks
on any failure, repo skeleton matching the architecture in `CLAUDE.md`.

**Gate:** CI green on an empty suite; lint and types clean.

---

## Phase 1 — Domain core (pure, no network)

**Goal:** the financial logic that decides whether and how much to trade, fully
isolated and property-tested. This is where correctness matters most.

**Deliverables:** typed data models; cost model + edge gate (abstain-by-default);
position sizing (fractional Kelly + hard caps); calibration layer
(isotonic / Platt); metrics (Brier, calibration curve, P&L, ROI).

**Gate:** unit + property tests for each component. Key invariants proven:
the gate never trades below cost; size never exceeds caps or bankroll;
calibration is monotonic and reduces Brier on overconfident inputs;
P&L accounting balances.

---

## Phase 2 — Data & backtest

**Goal:** replay history faithfully, with look-ahead bias made structurally
impossible.

**Deliverables:** Gamma/CLOB data adapters with recorded fixtures; reference-price
interface; walk-forward harness with strict time-ordering and out-of-sample
splits; deterministic replay.

**Gate:** adapters parse recorded payloads with no live network in tests; the
harness **rejects** a deliberately injected future-peek; same input yields
identical P&L on repeat runs.

---

## Phase 3 — Signals

**Goal:** the three edge sources as testable producers feeding the domain core.

**Deliverables:** cross-market consistency scanner (S2); lag/divergence signal
vs. reference price (S1); `pydantic-ai` hypothesis generator + feature extractor
emitting typed output.

**Gate:** each signal flags known synthetic mispricings and abstains within
noise; LLM output is schema-validated, mocked in tests, and malformed responses
never crash the loop.

---

## Phase 4 — Assembly & paper trading

**Goal:** the full pipeline running end-to-end with simulated fills and zero real
orders.

**Deliverables:** strategy wiring (signals → calibration → gate → sizing →
simulated fills); paper-trading mode over both historical and live data feeds.

**Gate:** an end-to-end integration scenario (a worked match with a known lag
event) produces the expected decisions; matches without edge produce ABSTAIN.

---

## Phase 5 — Execution & risk (testnet / dry-run)

**Goal:** real order mechanics validated without real funds, behind a hard risk
contour.

**Deliverables:** CLOB execution adapter (maker-first, allowance handling) on
Amoy testnet / dry-run; risk controls (max position, daily-loss cap) and a global
kill switch.

**Gate:** order payloads are correct against a mocked client; `tick_size` and
`minimum_order_size` violations are rejected; a simulated limit breach halts
trading and blocks further orders.

---

## Phase 6 — Live (tiny capital, gated)

**Goal:** validate the live path with real money treated as instrumentation.

**Deliverables:** live run with ~$20–25 at minimum order sizes, full logging and
reconciliation.

**Gate (operational):** clean end-to-end testnet run, then one monitored
micro-trade reconciled (expected vs. actual fill and slippage) before any further
use. Process metrics — calibration vs. no-vig market, post-cost EV in
walk-forward — are the real scorecard, not the live P&L.

---

## Status

- [x] Phase 0 — Foundation
- [ ] Phase 1 — Domain core
- [ ] Phase 2 — Data & backtest
- [ ] Phase 3 — Signals
- [ ] Phase 4 — Assembly & paper trading
- [ ] Phase 5 — Execution & risk
- [ ] Phase 6 — Live
