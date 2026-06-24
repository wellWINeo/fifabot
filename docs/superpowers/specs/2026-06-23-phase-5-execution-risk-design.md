# Phase 5 — Execution & risk (testnet / dry-run) (design)

Date: 2026-06-23
Status: approved (design); implementation pending

## Context

`PLAN.md` Phase 5 validates **real order mechanics without real funds**, behind a
hard risk contour. Phases 0–4 are complete: a property-tested pure core
(`core/`), offline Gamma/CLOB adapters plus a `ReferencePrice` Protocol with a
fixture-backed `ReplayReference` (`data/`), a look-ahead-safe replay / walk-forward
harness (`backtest/`), two wired signal producers (S1 `DivergenceStrategy`, S2
`ConsistencyStrategy`) feeding the Phase 1 `evaluate()` pipeline, a typed-only LLM
contract plus a mocked shadow S3 agent (`llm/`), and a thin async paper-trading
orchestrator with simulated maker fills (`app/orchestrator.py`).

What does **not** yet exist: `execution/` is empty (`__init__.py` only).
`data/clob.py` has public REST/WS reads and an L2 `ClobAuth.from_env`, but **no
EIP-712 order signing** (the file notes "full L2 request signing is Phase 5").
`core/models.py` `RiskLimits` feeds **sizing** only — there is no daily-loss cap
and no kill switch. `Market` has `tick_size` but no `minimum_order_size`.

**Goal (PLAN.md):**

- A CLOB **execution adapter** (maker-first, allowance handling) on Amoy
  testnet / dry-run.
- **Risk controls** (max position, daily-loss cap) and a **global kill switch**.

**Gate (PLAN.md):**

- Order payloads are correct against a **mocked** client.
- `tick_size` and `minimum_order_size` violations are **rejected**.
- A **simulated limit breach halts** trading and **blocks further orders**.

This spec records the shape so the implementation plan executes it without
re-deciding.

## Decisions

Settled with the user during brainstorming. Each records the decisive reason, not
just the choice.

1. **Posting depth — dry-run + signed payload, mock-tested; the entire gate stays
   offline (1A).** The deliverable is a path that builds → validates → EIP-712-signs
   the order and submits it only to a **mocked** `ExecutionClient`. *Reason:* the
   gate must be CI-coverable, and `CLAUDE.md` forbids real money/network until every
   prior gate passes; an in-phase real Amoy POST would drag keys/RPC into the gate
   and pre-empt Phase 6, while a sign-less path would leave the most error-prone
   surface unproven. The real on-chain round-trip is de-risked by a **non-gate,
   operator-run Amoy probe** (Decision/Component below), framed as a *mechanical
   pre-check* — Amoy cannot show a real fill, so the genuine fill-integration test
   remains the **Phase 6 mainnet micro-trade**, not this probe.

2. **Order signing — wrap `py-clob-client` v2 behind an in-house `ExecutionClient`
   Protocol (2A); EOA / signature type 0, `funder == signer`.** *Reason:* the SDK
   owns EIP-712 order-struct hashing, L2 HMAC headers, and nonce management for
   chain 80002/137; hand-rolling that secp256k1 surface for a ~$25 instrument is
   pure downside, and a stub cannot satisfy "payloads correct against a mock." The
   SDK is imported **only** inside `execution/client.py`; `core/` never sees it, and
   tests mock the Protocol. Signature **type 0 (EOA)** is chosen over type 1
   (Poly/Magic proxy) / type 2 (EIP-1271) for transparency and no funder/signer
   indirection; the consequence is **manual allowances** (Decision 3). The type is a
   wiring assumption baked into the order model; a future proxy wallet is a new
   `signature_type` + `funder` value, not a rearchitecture.

3. **Allowance handling — read-only precondition in venue preflight, returning a
   typed halt-this-market (3A).** *Reason:* an EOA must set USDC/CTF allowances once
   per wallet before trading; self-approving in-adapter would pull web3 + on-chain
   tx + gas management in for a once-per-wallet act. The submit path stays pure and
   mockable: preflight queries allowances and, if insufficient, returns a typed
   `INSUFFICIENT_ALLOWANCE` that the **risk layer treats as halt-this-market** (not
   a generic CLOB error). The actual approval is a one-off, documented operator
   script (`scripts/set_allowances.py`), outside the tested submit path. (Had we
   chosen a type-1 proxy in Decision 2, allowances would be automatic and this would
   degenerate to a near-noop; with EOA the precondition earns its keep.)

4. **Risk controls — pure brain in `core/risk.py`, thin enforcement in
   `execution/`, orchestrator routed through a venue seam now (4A).** *Reason:* the
   most important safety invariant ("when halted, no order can be placed") must be
   property-testable, so the decision math lives in pure `core/`; the enforcement,
   persistence, and preflight live at the `execution/` edge that calls it. This
   reconciles with `CLAUDE.md`'s architecture diagram (which lists "risk controls,
   kill switch" under `execution/`): `execution/` still *owns* risk controls
   operationally, but the math is financial logic and is factored into the pure core
   per "financial logic lives in pure functions." Because Decision 2 already builds
   the `ExecutionClient` seam, routing the orchestrator through an `ExecutionVenue`
   now (default `SimulatedVenue` = today's paper behavior) is the natural
   consequence, making Phase 6 a venue swap rather than an integration rewrite.

5. **Risk model — circuit-breaker-first, MTM daily-loss cap, sticky switch,
   persisted tripped flag.** *Reason / threat model:* on ~$25 a daily-loss cap can
   be most of the bankroll, so its real job is a **circuit breaker against a runaway
   loop** (e.g. a bug resubmitting orders), not protecting capital. Specifics:
   - **Max position** reuses `RiskLimits.max_position_usd` (per-market exposure).
   - **Daily-loss cap** counts **realized P&L of fills closed today + unrealized
     mark-to-market of open positions** — because positions close before resolution,
     an open losing position must be able to trip it — against an explicit **UTC day
     boundary** (`roll_day` resets the realized baseline at 00:00 UTC). Unrealized
     marks are fed from the latest quote per open market.
   - **Runaway-loop breakers (primary):** a hard **per-run order-count ceiling** and
     **rapid-resubmission detection** (min interval / max orders per market per
     window). These trip ahead of the loss threshold.
   - **Kill switch is sticky:** once `halted`, every `pretrade_check` rejects for the
     rest of the run; nothing un-halts it in-process.
   - **Persistence:** the pure core computes the halt **decision** from state; a
     durable store at the edge (`FileRiskStore`) persists the **tripped flag**, so a
     restarted process loads it and **comes up halted** — a crash-and-restart cannot
     silently bypass a fired cap. The Protocol + file impl + a "restart-while-halted
     stays halted" test ship in Phase 5; richer persistence is a Phase 6 ops concern.

6. **S2 basket gate — pure `core/basket.py` now, no model-error margin; atomic
   multi-leg execution deferred (option b).** *Reason:* Phase 3 earmarked "a
   near-riskless basket gate + atomic multi-leg execution" for Phase 5. The **gate**
   is pure, cheap, and captures the insight in code: for a mutually-exclusive group,
   `overround < 1` ⇒ long-the-set edge `1 − overround`; `> 1` ⇒ short-the-set edge
   `overround − 1`, gated against a hurdle that **excludes `model_error_margin`**
   (an accounting identity should not pay a probabilistic-error margin). The
   **execution** half is deferred: a CLOB has no native atomicity, so "atomic" means
   leg-by-leg submission with partial-fill unwind logic — a substantial surface that
   would otherwise leave unsafe non-atomic basket trading in the live path. The gate
   is therefore **property-tested in isolation and NOT wired** into any venue this
   phase; nothing consumes it until atomic execution lands.

7. **Nautilus deferred; `Market` gains `minimum_order_size`.** Nautilus stays a
   later thin wrapper (`CLAUDE.md`: "integrated late"); nothing in the Phase 5 gate
   needs an engine, and building the venue directly on `ExecutionClient` keeps the
   edge thin. `Market` gains a tick-aligned `minimum_order_size: Decimal` so the
   "min-size violations rejected" gate has a field to check.

## Components

`core/` never imports the SDK or `execution/`. The autouse DNS guard in
`tests/conftest.py` keeps a real socket failing loudly; the SDK client is only
constructed in operator scripts or behind a mock.

```
core/risk.py          pure RiskConfig, RiskState, transition/check fns (the brain)
core/basket.py        pure basket gate (no model-error margin); unwired this phase
execution/orders.py   OrderRequest / SignedOrder / OrderResult + pure validation
execution/client.py   ExecutionClient Protocol + ClobExecutionClient (only SDK importer)
execution/venue.py    ExecutionVenue seam: SimulatedVenue | ClobVenue (preflight→sign→submit)
execution/store.py    RiskStore Protocol + FileRiskStore (persists tripped flag)
app/orchestrator.py   parametrized by ExecutionVenue (default SimulatedVenue)
scripts/              set_allowances.py, probe_amoy_order.py (operator-run, non-gate)
```

### `core/risk.py` — the pure brain
- `RiskConfig` (frozen): `max_daily_loss_usd`, `max_orders_per_run`, resubmission
  window (min interval / max orders per market per window); reads
  `RiskLimits.max_position_usd` for the position cap.
- `RiskState` (frozen): `halted: bool`, `halt_reason: str | None` (the **global**
  sticky kill switch), `halted_markets: frozenset[str]` (**per-market** suppressions),
  `day: date` (UTC), `day_realized_pnl: Decimal`, open-position exposure for MTM,
  `orders_this_run: int`, recent per-market order timestamps.
- **Two halt scopes** (kept distinct to avoid one market's config gap killing the
  run): a **global** sticky kill switch (`halted`) reserved for account-level safety
  — daily-loss cap and runaway-loop breakers — and **per-market** suppression
  (`halted_markets`) for conditions local to one market, e.g. insufficient allowance.
  A `CheckResult` carries which scope it failed on. Global halt rejects every order;
  per-market halt rejects only that `market_id`.
- Pure transitions returning a new state and/or a typed result:
  `pretrade_check(state, config, order, now) -> CheckResult` (allow | typed halt with
  scope), `on_fill(state, fill) -> state`, `on_mark(state, marks, now) -> state`
  (trips the global switch on realized+unrealized loss ≥ cap),
  `roll_day(state, now) -> state`, `trip(state, reason) -> state`,
  `suppress_market(state, market_id, reason) -> state`. The global switch is sticky.

### `core/basket.py` — pure basket gate (unwired)
- Consumes a `MarketGroup`'s per-leg YES prices and `CostInputs`; reuses
  `core.signals.devig.overround`.
- A dedicated `basket_cost(...)` computes the near-riskless hurdle from real
  frictions (spread, fees, amortized gas) **excluding `model_error_margin`** by
  construction (intent in code, not a caller zeroing a field).
- `basket_decide(...) -> BasketDecision` (act long-set / act short-set / abstain,
  plus `basket_edge`), abstain-by-default. Property: for the same group
  `basket_hurdle < per_leg_hurdle`; still abstains when `|overround − 1|` is within
  real costs.

### `execution/orders.py` — order models + pure validation
- `OrderRequest` (token_id, side, price, size, `signature_type = 0`,
  `funder == signer`), `SignedOrder`, `OrderResult`/`OrderStatus`.
- `validate_order(order, market)` — price in (0,1), on `tick_size`,
  size ≥ `minimum_order_size`, size > 0. Off-tick / sub-min ⇒ typed rejection
  **before any signing or client call**.

### `execution/client.py` — the SDK seam
- `ExecutionClient` Protocol: `place(signed_order)`, `cancel(id)`, `status(id)`,
  `allowances()`.
- `ClobExecutionClient` wraps `py-clob-client` v2 (the only SDK importer), built
  for EOA/type-0 on chain 80002/137. Never imported by `core/`.

### `execution/venue.py` — the orchestrator seam
- `ExecutionVenue` Protocol: `place(order_request) -> OrderResult`.
- `SimulatedVenue` preserves today's `core/fills` paper behavior.
- `ClobVenue(client, risk, store)` runs the preflight pipeline (Data flow below).
  The injected `client` is a mock in the gate, a recording no-op in dry-run, the
  real SDK client only in the probe / Phase 6.

### `execution/store.py` — persistence
- `RiskStore` Protocol (`load() -> RiskState | None`, `save(state)`); `FileRiskStore`
  (JSON) at the edge; an in-memory fake for tests. Persists the tripped flag.

### Operator scripts (non-gate)
- `scripts/set_allowances.py` — one-off EOA USDC/CTF approval to the exchange.
- `scripts/probe_amoy_order.py` — posts one signed order on Amoy plus a
  self-counter-order and watches it settle. Mechanical pre-check only; mirrors the
  existing odds-api probes.

## Data flow — preflight pipeline (`ClobVenue.place`)

Short-circuits on the first failure; each step returns a **typed** outcome, never a
bare exception or raw CLOB error:

1. `validate_order` (pure) — tick / min-size / bounds. Reject **before** signing.
2. `risk.pretrade_check` — position cap, order-count ceiling, resubmission window,
   already-halted. Sticky halt on breach.
3. allowance preflight — `client.allowances()`; insufficient ⇒ typed
   `INSUFFICIENT_ALLOWANCE`, surfaced to risk as halt-this-market.
4. sign + submit — `ClobExecutionClient` builds the EIP-712 order (EOA/type-0) and
   submits. Mocked in the gate; real only in probe / Phase 6.

The orchestrator updates the risk controller on fills (`on_fill`) and on each new
quote for open markets (`on_mark`), persisting via `RiskStore` after transitions.

## Testing — the gate plus the agreed additions

1. **Payloads correct vs mocked client** — build `OrderRequest` from a `Decision`;
   assert the recorded payload (token_id, BUY_YES/BUY_NO → CLOB buy/sell of the
   correct token, price, size, `signature_type = 0`, funder) matches fixtures.
2. **tick / min-size violations rejected** — property tests on `validate_order`;
   assert the client is **never called** on rejection.
3. **Simulated limit breach halts + blocks further orders** — drive the controller
   past the daily-loss cap (via MTM), the per-run order-count ceiling, and the
   resubmission window; assert the switch trips and subsequent `place` calls reject
   without touching the client. Property: once halted, no input un-halts in-run.
4. **Restart-while-halted stays halted** — `FileRiskStore` round-trip.
5. **Runaway-loop breakers** — rapid-resubmission and per-run ceiling each trip.
6. **Basket gate (pure)** — flags a synthetic `overround ≠ 1` group, abstains within
   real costs, and `basket_hurdle < per_leg_hurdle` for the same inputs.
7. **No real network** — SDK client constructed only in scripts or behind a mock;
   the DNS guard remains.

## Out of scope / deferred (recorded so it is not lost)

- Real Amoy posting **in the gate** (operator probe only); the true fill-integration
  test is the **Phase 6 mainnet micro-trade**.
- Auto on-chain allowance approval (one-off operator script instead).
- **S2 atomic multi-leg basket execution** — the basket *gate* ships pure and
  unwired this phase; atomic leg-by-leg submission with partial-fill unwind is a
  dedicated follow-on before any basket can be acted on.
- Nautilus integration (later thin wrapper over the tested core).

## New dependency

`py-clob-client` (v2) — single `uv lock`. Pulls `eth-account`/web3 transitively
(used by the real client and the two operator scripts). Accepts chain 80002 (Amoy)
and 137.
