# fifabot

Automated trading research for Polymarket soccer markets (2026 FIFA World Cup).
See `CLAUDE.md` for project framing and rules, `PLAN.md` for the phased roadmap.

## Development

The toolchain is pinned via a Nix flake (Python 3.12 + uv).

```bash
nix develop          # enter the devshell (or `direnv allow` once, then automatic)
uv sync              # install dependencies into .venv

uv run ruff check          # lint
uv run ruff format --check # format check
uv run mypy                # types
uv run pytest              # tests

pre-commit install   # enable commit-time gates (ruff, mypy, gitleaks)
```

## Phase 5 operator steps (testnet, non-gate)

These touch the live network and require env credentials (`WALLET_PRIVATE_KEY`,
`PROBE_TOKEN_ID`); they are never run in CI.

1. One-off allowance approval (once per wallet):
   `nix develop --command uv run python -m scripts.set_allowances`
2. Amoy order probe (mechanical pre-check that a signed order settles):
   `nix develop --command uv run python -m scripts.probe_amoy_order`

A real *fill* is only validated by the Phase 6 mainnet micro-trade — Amoy
cannot demonstrate one.
