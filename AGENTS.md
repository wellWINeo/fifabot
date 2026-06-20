# CLAUDE.md

Project context and operating rules for any Claude Code agent working in this
repository. Read this in full before planning or writing code.

---

## Project

A research-grade automated **trading** system for Polymarket soccer markets,
targeting the 2026 FIFA World Cup. The purpose is to learn prediction-market
microstructure and agentic signal generation — **not** to maximize profit.
Operating capital is ~$25; treat it as instrumentation, not a bankroll.

### Core framing — read before anything else

- **Trading, not betting.** We profit from *price movement* (mispricing, market
  lag, cross-market inconsistency), not from predicting match outcomes.
  Positions are normally closed **before** resolution to shed match-outcome
  variance. Polymarket is a CLOB exchange; a contract is a tradable instrument,
  not a locked-in wager.
- **Default action is ABSTAIN.** The bot does nothing unless a *promoted* signal
  clears the cost gate. It will act on a small minority of matches, and that is
  correct. A bot that trades every match loses the spread by construction.
- **Success is process, not P&L.** The bar is: calibrated probabilities beating
  the no-vig market, positive post-cost EV in walk-forward, and signals that
  survive out-of-sample. Live P&L on ~$25 is statistically meaningless and only
  serves as an infrastructure smoke test.
- **Pre-match focus.** No live in-play latency loop — we cannot beat broadcast
  delay, so reactive live trading on commentary/news is out of scope.

### Edge sources (in priority order)

1. **Cross-market consistency** (arbitrage): sum of mutually exclusive YES
   prices deviating from ~1.0 across linked markets. No outcome view required.
2. **Market lag / divergence**: thin Polymarket price trailing a sharp
   reference (e.g. Betfair Exchange) or slow to absorb public lineup news.
3. **Outcome forecasting**: lowest priority, hardest path. Only trades if it
   survives walk-forward; otherwise it stays silent.

---

## Stack

- **Language / env:** Python 3.12+, managed with `uv`.
- **Domain core (pure, testable):** `pydantic` v2 (typed models),
  `numpy` / `scipy` / `scikit-learn` (calibration, Kelly math), `polars`
  (historical data for backtests).
- **LLM layer:** `pydantic-ai` — hypothesis generation and feature extraction
  into typed structures. Model-agnostic; mockable in tests.
- **Data / execution:** `httpx` (async) + `websockets` (Gamma / CLOB REST and
  WS); `py-clob-client-v2` / `py-sdk` for order signing and posting;
  `nautilus_trader` as the backtest/live engine, integrated late as a thin
  wrapper over the tested domain core.
- **Quality gates:** `pytest` + `pytest-cov`, `hypothesis` (property tests),
  `ruff` (lint), `mypy` (types).

---

## Architecture

Hexagonal: pure domain logic at the center, IO and frameworks at the edges.
Framework code (Nautilus strategies, live execution) must be **thin wrappers**
that call already-tested pure functions.

```
core/        cost_model, edge_gate, sizing (Kelly + caps), calibration, signals, metrics
data/        gamma / clob / historical adapters, reference-price interface
backtest/    walk-forward harness (look-ahead guard), metrics reporting
llm/         pydantic-ai hypothesis generator + feature extractor
execution/   CLOB order placement (maker-first), risk controls, kill switch
app/         asyncio orchestration of the staged loop
```

---

## Non-negotiable rules

**Engineering**
- **TDD always.** Write the failing test first; no step is complete until its
  tests are green plus `ruff` and `mypy` are clean. Do not advance to the next
  plan step with a red gate.
- **No real network in unit tests.** Use recorded fixtures (VCR-style) for data
  adapters; mock the LLM and the CLOB client.
- **Pure core, thin edges.** Financial logic lives in pure functions that are
  unit- and property-tested in isolation.

**Domain correctness (these prevent losing money)**
- **No look-ahead bias, ever.** The backtest may only expose data with a
  timestamp ≤ the current simulated time. There must be a test that deliberately
  injects future-peeking and asserts the harness forbids it.
- **Cost gate is law.** Never emit a trade when
  `edge < round_trip_cost + model_error_margin` (spread + fee + gas + margin).
  This invariant is property-tested.
- **Calibrate before use.** LLM/model probabilities pass through a calibration
  layer (isotonic / Platt) before any decision. The LLM is a feature extractor
  and hypothesis generator — **never** the final arbiter of probability or size.
- **Maker-first execution.** Prefer resting limit orders over crossing the
  spread. Respect `tick_size` and `minimum_order_size`; invalid orders must be
  rejected before submission.
- **Overfitting guard.** Promote a signal only after walk-forward + out-of-sample
  validation with a multiple-testing correction. Retire signals whose live
  track record decays.

**Safety / ops**
- **No real money until every prior gate passes.** Testnet (Amoy, chain 80002)
  or dry-run first; a single monitored micro-trade before any scaled use.
- **Hard risk caps + kill switch** are mandatory before live: max position, max
  daily loss, global abstain toggle. When halted, no order can be placed.
- **Secrets never touch git.** Private keys and API credentials come from env
  vars only. No keys in code, logs, fixtures, or commits.

---

## Working agreement

- Keep changes small and reviewable; one plan step per worktree/branch.
- Update `PLAN.md` status as steps complete.
- When a domain rule above is in tension with a request, the rule wins — surface
  the conflict rather than silently working around it.
