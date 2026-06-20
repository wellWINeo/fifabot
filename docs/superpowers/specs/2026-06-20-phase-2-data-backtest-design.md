# Phase 2 — Data & backtest (design)

Date: 2026-06-20
Status: approved (design); implementation pending

## Context

`PLAN.md` Phase 2 makes history replayable with **look-ahead bias structurally
impossible**, and gives the system its first IO edge. Per `CLAUDE.md` this is the
"thin edge" of the hexagon: network lives only in `data/`; `backtest/` is pure
replay over typed records that reuses the Phase 1 core (`core/decision.py`
`evaluate`, `core/metrics.py`) unchanged. Phase 0 (foundation) and Phase 1
(domain core) are complete: a nix-managed uv project with the flat module
skeleton, strict `mypy`/`ruff`, `pytest` + `pytest-cov` + `hypothesis`, CI and
pre-commit gates, and a fully property-tested pure core.

**Goal:** full async Gamma/CLOB data clients (tested entirely offline), a
reference-price interface plus a fixture-backed replay implementation, and a
walk-forward harness with strict time-ordering, out-of-sample splits, and
deterministic replay — with the look-ahead guard enforced *structurally*, not by
discipline.

This spec records the shape so the implementation plan executes it without
re-deciding.

## Decisions

These four were settled with the user during brainstorming:

- **Adapter scope: full async clients.** Gamma and CLOB get complete async
  clients (pagination, bounded retry/backoff, a simple async rate-limiter,
  configurable base URL, optional auth headers, CLOB WS subscriptions) — not a
  parse-only layer. This is broader than the gate strictly requires and is in
  mild tension with `CLAUDE.md`'s "thin edges" / "pre-match focus"; the tension
  is resolved by keeping **every** network call behind an injected, mockable
  transport so the test suite never touches a socket, and by treating CLOB WS as
  built-now / consumed-in-Phase-4.
- **Look-ahead guard: push-based event replay + as-of view.** The harness owns
  the clock and pushes chronologically-sorted events one at a time; the strategy
  only ever receives an immutable `MarketView` bound to the current event's
  timestamp that **raises** on any query for `ts > as_of`. The future is not in
  scope. Chosen over a pull-only as-of view (less faithful to live operation) and
  over a timestamp-assert-only guard (a check you must remember to call, not a
  structural barrier).
- **Reference price: Protocol + replay impl.** Define a `ReferencePrice` Protocol
  and a fixture-backed `ReplayReference` that aligns recorded reference quotes to
  the event stream by timestamp, under the same as-of guard. A live Betfair
  adapter (account, cert login) is deferred to the phase whose signal first
  consumes it (Phase 3+).
- **Offline test mechanism: built-in `httpx.MockTransport` + injected WS
  factory.** REST clients accept an injected `httpx` transport (`MockTransport`
  in tests); the WS client accepts an injected `ws_connect` factory (a fake async
  connection in tests). No new dev dependency — `MockTransport` ships with
  `httpx`, satisfying `AGENTS.md` ("don't add a dependency for what existing
  libraries already do"). The "VCR-style fixtures" intent in `CLAUDE.md` is read
  as "replay recorded payloads, never hit the network," which this satisfies.
  For the Phase 2 gate, fixtures are **hand-authored representative payloads**
  committed as JSON (the implementing agent cannot reach the network). A
  `scripts/record_fixtures.py` helper that captures and sanitizes **real**
  payloads is an optional, human-run extra (network allowed, run outside the
  suite) — useful for later phases, not part of the gate.

Additional decisions made in the design:

- **Canonical `MarketEvent` is the data/↔backtest/ boundary.** Adapters parse raw
  payloads into typed, timestamped canonical records; the harness only ever sees
  those records. The backtest never imports an adapter or `httpx`.
- **Minimal deterministic fill model in Phase 2.** The gate needs P&L to prove
  determinism, but realistic simulated fills / slippage belong to Phase 4 (paper
  trading). The engine here fills the decided stake at the event price and closes
  at a later event's price — just enough to exercise `core.metrics` and prove
  identical P&L on repeat runs. The richer fill model is Phase 4.
- **`Strategy` is a Protocol.** The harness runs a synthetic strategy now; the
  real Phase 3 signals / Phase 4 assembled pipeline plug into the same interface
  later. Phase 2 does not implement any real signal.
- **New runtime deps: `httpx`, `websockets`, `polars`.** First phase that needs
  them. No new dev deps. `uv lock` before any commit so CI `--frozen` stays green.
- **Numeric representation carries over from Phase 1.** `Decimal` for prices /
  quotes / cash / P&L (exact tick + accounting); `float` only for statistical
  math. Timestamps are timezone-aware UTC `datetime`.

## Components

### data/ — adapters (network only here)

All network calls go through an injected transport; nothing in `data/` is
imported by `backtest/`.

#### 1. `data/payloads.py` — raw API models

pydantic v2, `frozen=True`, validated at construction. Mirrors the wire shapes we
actually consume (not the whole API):

- Gamma: market metadata (id, question, linked-market grouping fields, token
  ids, `tick_size`, active/closed flags), price-history point (`t`, `p`).
- CLOB: order-book snapshot (`market`, `asks`, `bids` as price/size levels),
  price-history point. Unknown extra fields ignored; missing required fields
  raise `ValidationError`.

#### 2. `data/events.py` — canonical records

The typed, timestamped records the harness replays. pydantic `frozen=True`:

- `Market` — slim canonical market metadata: `market_id: str`, `question: str`,
  `token_ids: tuple[str, ...]`, `tick_size: Decimal`, `active: bool`. (Linked-
  market grouping for cross-market arbitrage is Phase 3; only fields Phase 2
  consumes are modeled.)
- `Quote` — `market_id: str`, `ts: datetime` (UTC), `price: Decimal` in `(0, 1)`,
  optional `bid`/`ask`/`size: Decimal`. Top-of-book observations and price-history
  points are both represented as `Quote`s. The unit a `MarketView` serves.
- `MarketEvent` — `ts: datetime` (UTC), `market_id: str`, `quote: Quote`. The
  unit the feed sorts and the engine pushes. (Book snapshots ride in the quote's
  optional `bid`/`ask`/`size`; a richer book event type is deferred until a Phase
  4 consumer needs it.)
- A small helper to build canonical events from parsed payloads.

Bulk historical loading lives alongside in `data/history.py`: `polars`
conversions (`quotes_to_frame` / `frame_to_events`) so recorded CSV/parquet
series become an ordered event stream. This is the one place `polars` is used.

#### 3. `data/gamma.py`

- `parse_market(raw: GammaMarket) -> Market` and
  `parse_price_history(raw) -> list[Quote]` — **pure**, fixture-tested. Off-tick
  or out-of-range prices rejected via the Phase 1 models where applicable.
- `class GammaClient` — async, over `httpx.AsyncClient` built from an injected
  `transport`. Methods to fetch markets (with pagination) and price history.
  Bounded retry/backoff on transient errors; a simple async rate-limiter; base
  URL configurable. Gamma reads are public (no auth required) but headers are
  pluggable. Returns parsed canonical records, not raw payloads. The shared
  rate-limiter and retry helper live in `data/http.py` (used by both clients,
  DRY).

#### 4. `data/clob.py`

- `parse_book(raw) -> Quote` (top-of-book → quote with `bid`/`ask`/`size`) and
  `parse_price_history(raw) -> list[Quote]` — pure, fixture-tested.
- `class ClobClient` — async REST (book, price history) with the same injected
  transport, retry, and rate-limiter machinery. Optional auth headers
  (`CLOB_API_KEY` / `SECRET` / `PASSPHRASE`) read from **env only**, never from
  fixtures, logs, or code; public market-data reads need no auth.
- `class ClobWsClient` — subscription client accepting an injected `ws_connect`
  factory. Yields parsed `Quote`s. Built now, consumed in Phase 4; tested against
  a fake async connection that replays recorded frames.

#### 5. `data/reference.py`

- `class ReferencePrice(Protocol)` — `at(self, market_id: str, ts: datetime) ->
  Decimal | None`: the latest reference price at or before `ts`, or `None`.
- `class ReplayReference` — fixture-backed; holds timestamped quotes per market;
  `at` returns the latest quote with `quote.ts <= ts` and **never** anything
  later. Same as-of discipline as the harness.

### backtest/ — pure replay (no network, no adapter imports)

#### 6. `backtest/feed.py`

- `load_events(events: Iterable[MarketEvent]) -> list[MarketEvent]` —
  **validate** that events are non-decreasing in `ts`, returning them as a list;
  an out-of-order event raises `LookAheadError`. We reject rather than silently
  sort, so a mis-ordered feed cannot mask a look-ahead bug. Producing a merged
  chronological stream across markets is the caller's job (a heap-merge helper is
  deferred until a multi-market consumer needs it). First line of the structural
  guard.

#### 7. `backtest/view.py`

- `class MarketView` — immutable, bound to `as_of: datetime`, holding accumulated
  history up to `as_of`. Query methods (`price_at(ts)`, `latest_price()`,
  `reference_at(ts)`, …) **raise `LookAheadError`** for any `ts > as_of`. The
  strategy holds only a view, so the future is unreachable by construction.

#### 8. `backtest/strategy.py`

- `class Strategy(Protocol)` — `on_event(self, event: MarketEvent, view:
  MarketView) -> Decision | None`. The seam between the harness and signals.
  Phase 2 ships only a synthetic test strategy.

#### 9. `backtest/engine.py`

- `replay(events, strategy, limits, *, reference=None) -> BacktestResult` — drive
  the sorted feed: per event, extend the `MarketView(as_of=event.ts)`, call
  `strategy.on_event`, and for any `ACT` decision open/close a position under the
  **minimal deterministic fill model**, accounting P&L via `core.metrics`. No
  wall-clock, no RNG → identical `BacktestResult` on repeat runs.
- `class BacktestResult` — fills, realized P&L, ROI, and calibration/Brier inputs;
  consumed by `report.py`.

#### 10. `backtest/walkforward.py`

- `walk_forward_splits(n, *, train_size, test_size, step=None, expanding=False)
  -> list[Split]` — pure splitter over the sorted-event index space `[0, n)` into
  `Split(train: range, test: range)` windows where `test.start == train.stop`
  (test strictly after train, no overlap, no leakage). `step` defaults to
  `test_size` (non-overlapping test windows); `expanding=True` anchors every
  train window at index 0. The caller maps index ranges onto its sorted
  events/timestamps. Index-based for deterministic, fence-post-free splitting.

#### 11. `backtest/report.py`

- Aggregate per-split P&L and ROI into a `WalkForwardReport` (reusing
  `core.metrics`). Thin — reporting only, no new financial logic. Brier
  aggregation arrives once signals emit probabilities (Phase 3).

## Data flow (one backtest)

1. Hand-authored representative payloads live as JSON under
   `tests/fixtures/{gamma,clob}/` (optionally replaced later by sanitized real
   captures via `scripts/record_fixtures.py`).
2. `parse_*` turn fixtures (or live client responses) into canonical
   `MarketEvent` / `Quote` records and reference quotes.
3. `feed.load_events` sorts and asserts ordering (rejects future-injection).
4. `engine.replay`: per event, build `MarketView(as_of=event.ts)`; the strategy
   decides via the Phase 1 core (`evaluate`); deterministic fills; P&L via
   `core.metrics`.
5. `walkforward.walk_forward_splits` partitions the timeline; the engine runs per
   split; `report` aggregates.

## Test plan (the Phase 2 gate)

Each item maps to a `PLAN.md` Phase 2 invariant. Property tests use `hypothesis`.
TDD: each component is written red → green with its tests before the next.

- **No live network in tests** (all `data/` tests): every client test uses
  `httpx.MockTransport` / an injected fake WS; an autouse guard fails any test
  that would open a real connection. Parse tests read only committed fixtures.
- **Adapters parse recorded payloads** (unit, `gamma`/`clob`): each fixture
  round-trips into typed canonical records; malformed payloads raise cleanly.
- **Look-ahead is structurally rejected** (the `CLAUDE.md` non-negotiable):
  - (unit, `view`) `MarketView` query for `ts > as_of` raises `LookAheadError`.
  - (unit, `feed`) a deliberately future-injected / out-of-order event raises
    `LookAheadError`. This is the required "inject a future-peek, assert the
    harness forbids it" test.
- **Deterministic replay** (unit/property, `engine`): the same input yields an
  identical `BacktestResult` (and identical P&L) across repeat runs.
- **Walk-forward correctness** (property, `walkforward`): splits are time-ordered
  and non-overlapping, every test window starts strictly after its train window
  ends, and no record appears in both — no leakage.
- **Client behavior under the mock** (unit, `gamma`/`clob`): pagination assembles
  multiple pages into one result; retry recovers from a transient error then
  succeeds; the rate-limiter bounds in-flight requests.
- **Reference replay respects as-of** (unit, `reference`): `at(market_id, ts)`
  returns the latest quote `<= ts` and never a later one.

## Implementation ordering

1. Add `httpx`, `websockets`, `polars` to `pyproject.toml`; `uv lock`; `uv sync`
   (all three ship `py.typed`, so no mypy overrides are expected).
2. `data/payloads.py` (+ tests) and `tests/conftest.py` network guard — raw API
   models; parsers depend on them.
3. `data/events.py` + `data/history.py` (+ tests) — canonical records (the
   backtest boundary) and the `polars` bulk-load conversions.
4. `data/http.py` (+ tests) — shared `RateLimiter` + bounded-retry `get_json`.
5. `data/gamma.py` (+ tests) — pure parsers first (fixtures), then `GammaClient`
   over `MockTransport` (pagination, retry, rate-limit).
6. `data/clob.py` (+ tests) — pure parsers, then `ClobClient` (REST) and
   `ClobWsClient` (injected fake WS).
7. `data/reference.py` (+ tests) — Protocol + `ReplayReference`.
8. `backtest/feed.py` (+ tests) — chronology validation + future-inject
   rejection.
9. `backtest/view.py` (+ tests) — as-of guard.
10. `backtest/engine.py` + `backtest/strategy.py` (+ tests) — the `Strategy`
    Protocol, replay loop, minimal deterministic fills, determinism.
11. `backtest/walkforward.py` (+ tests) — index-based splitter property tests.
12. `backtest/report.py` (+ tests) — per-split aggregation over `core.metrics`.
13. (Optional) `scripts/record_fixtures.py` — human-run real-payload recorder;
    network-using, not part of the test suite or the gate.

Each step is red → green plus `ruff` and `mypy` clean before the next. Any
dependency change is followed by `uv lock` before commit, or CI `--frozen` fails.

## Acceptance gate

Inside `nix develop`:

- `uv run ruff check` clean; `uv run ruff format --check` clean.
- `uv run mypy` clean (strict).
- `uv run pytest --cov` green, including every test above, with **no live
  network** reachable from any test.
- CI encodes the same gates and is green.

Matches `PLAN.md` Phase 2: "adapters parse recorded payloads with no live network
in tests; the harness rejects a deliberately injected future-peek; same input
yields identical P&L on repeat runs."

## Out of scope (Phase 2)

- Real signal producers — cross-market consistency, lag/divergence, LLM
  hypothesis generation (Phase 3).
- Live Betfair (or any live reference) adapter — deferred until a signal consumes
  it.
- Nautilus integration — a thin wrapper over this tested harness, added late
  (Phase 4/5).
- Realistic simulated fills / slippage and live-feed paper trading (Phase 4).
- Live CLOB WS consumption in the running app — the client is built and
  fixture-tested now, but wired into the loop in Phase 4.
- Real order execution, risk caps, kill switch (Phase 5).
