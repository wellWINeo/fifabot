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
