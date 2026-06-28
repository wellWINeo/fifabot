# FIFA 2026 market auto-discovery — design

Status: design (pre-implementation). Date: 2026-06-27.

## Purpose

Enumerate the Polymarket market universe for the 2026 FIFA World Cup so a later
data-collection step has a concrete, classified set of markets to pull price
history for. This is the first piece of the real-data walk-forward backtest (see
`docs/backtesting.md`). Discovery **enumerates and classifies**; it does not
decide what to trade, does not extract resolved outcomes, and does not pull
price history.

## Motivation & scope decision

The 104-match World Cup is a small universe, and the bot abstains by default, so
a match-only set yields too few observations to validate a signal. The backtest
scorecard — calibration Brier vs. the no-vig market, post-cost EV in
walk-forward, survival under a multiple-testing correction — needs sample size.

Therefore discovery casts a **wide net**: every World-Cup-related market
(per-match moneylines, tournament outrights, group winners, advancement,
top-scorer, standalone props), and **classifies** each by which signal can
consume it. This widening is justified by *backtest statistical power*, not by a
desire to trade more — abstain-by-default and "success is process, not P&L"
(`CLAUDE.md`) still hold.

Eligibility per signal:

- **S2 (consistency)** — any negRisk group (legs sum to ~1). The main
  beneficiary of the wide net.
- **S1 (divergence)** — reference-bound; only per-match moneylines have a sharp
  odds-api reference. Discovery tags `match_moneyline` as a *structural hint*;
  true S1 eligibility is settled later when reference-matching runs.
- **S3 (forecast, shadow/unpromoted)** — can log on anything, never trades.

## Topic-match mechanism

Topic is matched via the **Gamma tag/series filter**, not keyword matching and
not a hybrid. Rationale: precise and stable. Accepted tradeoff: any World-Cup
market Polymarket failed to tag is missed.

The exact tag/series parameter is **not yet known** and must be confirmed before
implementation (see "Probe-first" below). It is then pinned as a module-level
default, overridable by parameter.

## Probe-first prerequisite (do before writing `data/discovery.py`)

`scripts/probe_gamma.py` (human-run, real network, not in the test suite) must
confirm three things the design depends on:

1. **The tag/series param** — is it `tag` (slug), `tag_id` (numeric), or
   `series_id`, and does it actually return a filtered set? (`probe_gamma.py`
   currently *guesses* `{"tag": "soccer"}` and falls through unfiltered.)
2. **The event-nested market field shape** — confirm `clobTokenIds` is a
   JSON-encoded string (it is, per `tests/fixtures/gamma/events_negrisk.json`),
   and which of `tickSize` / `minimumOrderSize` / `closed` / a start-time field
   are present on the nested market vs. only on top-level `/markets`.
3. **A robust match signal** — does a field exist
   (`startDate` / `gameStartTime` / `sportsMarketType` / `gameId`) that
   distinguishes a 3-way match moneyline from a 3-leg outright/group-winner
   negRisk event? Leg-count + negRisk flag cannot (a match moneyline and the
   "World Cup Winner" outright are both 3-leg negRisk).

Findings are recorded and pinned as config. If no robust match signal exists,
`match_moneyline` tagging is degraded to "best effort" and that is stated
explicitly; S1 eligibility is still finally decided at reference-matching time.

## Architecture

New module `data/discovery.py`, following the existing `data/` pattern: pure
parsers/classifiers + a single thin network-touching orchestrator. Recorded
fixtures in tests, no live network (autouse guard in `tests/conftest.py`).

```
discover_fifa_markets(tag, topic)          ── async, the ONLY network surface
    │  GammaClient.fetch_events(params={tag: ...})  → raw GammaEvent[]
    ▼
classify_event(event)                      ── pure: event + legs → kind + group_id
    │  DiscoveredMarket[]  (one per leg)
    ▼
build_manifest(topic, tag, markets, groups, discovered_at)   ── pure
    │  DiscoveryManifest
    ▼
write_manifest(manifest, dir)              ── JSON snapshot under var/discovery/
```

Downstream: `load_latest_manifest(dir)` returns the newest snapshot for the
collection step.

## Components

- **`discover_fifa_markets` (orchestrator)** — calls the client, runs pure
  classification + manifest build, persists. Raises on an empty/below-floor
  result. The only part touching the network.
- **`classify_event` (pure)** — maps one `GammaEvent` + legs to
  `DiscoveredMarket[]`, each with `kind ∈ {match_moneyline, outright,
  group_winner, prop, other}` and `group_id` (the event id when negRisk, else
  `None`). `kind="other"` is the safe default; `match_moneyline` is assigned
  only on the probe-confirmed robust signal, never leg-count alone. Never raises.
- **`build_manifest` (pure)** — collects `DiscoveredMarket[]`, dedups by
  `market_id`, sorts deterministically, and attaches the negRisk `MarketGroup[]`
  built by the existing `parse_event_groups` (whose `group_id` equals the event
  id, matching each leg's `group_id`).
- **`write_manifest` / `load_latest_manifest`** — JSON snapshot I/O.

## Data model changes

New frozen pydantic records in `data/events.py` (beside `Market`/`MarketGroup`):

- `DiscoveredMarket` — `market_id`, `question`, `token_ids: tuple[str, ...]`,
  `event_slug`, `kind: str`, `group_id: str | None`.
  Deliberately **no** `tick_size` / `minimum_order_size` (execution concerns,
  absent from the nested shape, and including them would force an N+1
  `/markets/{id}` fetch) and **no** `resolved` (resolution is owned by the
  separate labelling step; the nested market has no clean `closed`).
- `DiscoveryManifest` — `topic: str`, `tag: str`, `discovered_at: AwareDatetime`,
  `markets: tuple[DiscoveredMarket, ...]`, `groups: tuple[MarketGroup, ...]`.

Payload/adapter changes:

- **`GammaEventMarket`** (`data/payloads.py`) — add `clobTokenIds: list[str]`
  with a `field_validator(mode="before")` that `json.loads` a `str` into a list
  (raising clearly on malformed input), plus any probe-confirmed fields
  (`outcomes`, start-time). Keep `extra="ignore"`; carry the `# noqa: N815`
  convention for wire names.
- **`GammaClient.fetch_events`** (`data/gamma.py`) — add an optional merged
  `params: Mapping[str, str] | None = None` (or explicit `tag`), default-
  preserving so the existing `fetch_event_groups` caller is unaffected.

## Persistence & freshness

- One append-only JSON snapshot per run:
  `var/discovery/fifa-2026-<discovered_at:%Y%m%dT%H%M%SZ>.json`. Never
  overwrites. (A top-level `var/` keeps generated run-artifacts out of the
  importable `data/` source package, avoiding a name collision with the new
  `data/discovery.py` module.)
- `var/` is gitignored — a generated artifact, reproducible by re-running.
- Markets sorted by `market_id`, so two runs over the same universe differ only
  in the capture timestamp (content is diffable; N3).
- Re-runnable as the tournament progresses (knockout fixtures appear, markets
  resolve). Each run is a fresh snapshot; `load_latest_manifest` exposes the
  newest. No mutable merge.

## Error handling

- Network: reuse `data/http.get_json` (retry / backoff / rate-limit). No new
  network logic.
- Empty/implausible result: orchestrator raises (a wrong/missing tag writing an
  empty manifest is the realistic silent failure).
- Payload parsing may raise on a missing declared field (surfaces API drift,
  consistent with existing adapters). Classification never raises — unknown
  shapes → `kind="other"`.
- `clobTokenIds` decode failure raises a clear error.

## Test plan (TDD, fixture-based, no network)

Extend `tests/fixtures/gamma/` with a mixed FIFA events fixture: a match
moneyline, an outright winner, a group winner, a standalone prop, and one non-WC
event (proves filtering/classification excludes it).

- `GammaEventMarket`: JSON-string `clobTokenIds` decodes to a list; malformed
  string raises.
- `classify_event`: table-driven over every `kind`; ambiguous/unknown → `other`;
  negRisk groups detected; `match_moneyline` only on the robust signal; never
  raises on weird input.
- `fetch_events(params=...)`: `MockTransport` asserts the `tag` param is sent and
  merged with `limit`/`offset`; the default call is unchanged (regression guard
  for `fetch_event_groups`).
- `build_manifest`: dedups by `market_id`; internally consistent (every
  every `group.market_ids` element ∈ `markets`); deterministic ordering.
- `discover_fifa_markets`: with a fixture transport, builds the expected
  manifest (markets/groups/kinds); raises on an empty result.
- `write_manifest` / `load_latest_manifest`: round-trips; content stable modulo
  timestamp.

## Out of scope (owned by later steps)

- Resolved 0/1 outcome labelling.
- Price-history collection / persistence.
- Reference (odds-api) matching that finally decides S1 eligibility.
- The walk-forward runner and scorecard.

## Open items (resolved by the probe)

- Exact tag/series param name and value.
- Event-nested market field shape (token ids string form confirmed; start-time /
  resolution fields TBD).
- Whether a robust `match_moneyline` discriminator exists; if not,
  `match_moneyline` is best-effort and documented as such.
