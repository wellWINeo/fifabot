# Phase 0 — Foundation (design)

Date: 2026-06-20
Status: approved (design); implementation pending

## Context

`PLAN.md` Phase 0 establishes a repo where "tests after every step" is enforced
by machinery, not discipline. The repo currently holds only `AGENTS.md`
(symlinked to `CLAUDE.md`) and `PLAN.md`, and is not yet a git repository. This
spec records the foundation's shape so the implementation plan can execute it
without re-deciding.

**Goal:** a reproducible, nix-managed toolchain plus the green quality gate
(`ruff` + `mypy` + `pytest`) wired into both pre-commit and CI, over a repo
skeleton matching the architecture in `CLAUDE.md`.

## Decisions

- **Package layout:** flat top-level modules — `core/ data/ backtest/ llm/
  execution/ app/` at repo root (matches `CLAUDE.md` literally). Not a
  distributable package; `[tool.uv] package = false`.
- **Python:** pinned to 3.12, provided by a **nix flake devshell** (nix owns the
  `python` + `uv` versions). `requires-python = ">=3.12"`.
- **Dependencies installed now:** dev tooling (`pytest`, `pytest-cov`,
  `hypothesis`, `ruff`, `mypy`, `pre-commit`) + `pydantic>=2`. All other domain
  deps (numpy, scipy, scikit-learn, polars, pydantic-ai, httpx, websockets,
  nautilus_trader, clob clients) are deferred to the phase that first uses them.
- **CI:** GitHub Actions, running gates **via uv directly** (`astral-sh/setup-uv`),
  not inside `nix develop`. Chosen for speed; accepts that CI does not use the
  nix devshell. `git init` is part of this phase.
- **README:** minimal dev-setup README included.
- **License:** MIT — `LICENSE` file plus `license = "MIT"` in `pyproject.toml`.

## Components

### 1. Toolchain — nix flake devshell
- `flake.nix`: a `devShell` exposing `python312` and `uv`, pinned via
  `flake.lock`. The shell env pins uv to the nix interpreter explicitly —
  `UV_PYTHON=${python312}/bin/python3.12`, `UV_PYTHON_PREFERENCE=only-system`,
  and `UV_PYTHON_DOWNLOADS=never` — so uv manages the venv/deps inside the nix
  Python instead of fetching a standalone one. (`UV_PYTHON_DOWNLOADS=never`
  alone only *forbids* downloads; `UV_PYTHON` + `only-system` is what actually
  selects the nix interpreter.) This env applies only in the devshell; CI is
  unaffected and lets uv fetch Python 3.12.
  - *Caveat:* on a NixOS host, manylinux wheels with vendored shared libraries
    can fail to load against nix glibc. In Phase 0 the only compiled dep is
    `pydantic-core` (a manylinux wheel) and the dev host is macOS, so blast
    radius is nil now; revisit when numpy/scipy/nautilus arrive in later phases.
- `.envrc` containing `use flake` for direnv auto-activation.
- `.python-version` = `3.12` as a backstop.

### 2. uv project
- `pyproject.toml`: project metadata, `requires-python = ">=3.12"`,
  `[tool.uv] package = false`.
- Runtime dependency: `pydantic>=2`.
- Dev dependency group: `pytest`, `pytest-cov`, `hypothesis`, `ruff`, `mypy`,
  `pre-commit`.
- `uv.lock` committed for reproducibility.

### 3. Repo skeleton
- `core/ data/ backtest/ llm/ execution/ app/`, each with `__init__.py`.
- `tests/` mirroring those subdirectories, plus `conftest.py`.
- One trivial smoke test importing each package. Rationale: pytest exits code 5
  on zero collected tests, which CI reads as failure — so "green on an empty
  suite" requires one passing test, which doubles as proof the skeleton imports.

### 4. Quality-gate config (in `pyproject.toml`)
- **ruff:** `target-version = py312`, lint rule sets (E, F, I, UP, B, …) plus the
  formatter; `[tool.ruff.lint.isort] known-first-party = ["core","data",
  "backtest","llm","execution","app"]` so import grouping treats the flat
  modules as first-party.
- **mypy:** strict, `python_version = 3.12`, `plugins = ["pydantic.mypy"]`,
  `files` covering the six modules + `tests`. Because the layout is flat (not an
  installed package), also set `mypy_path = "."`, `explicit_package_bases =
  true`, `namespace_packages = true` so module resolution is unambiguous. Start
  strict on tests; if hypothesis decorators / fixtures make it noisy, relax via
  a `[[tool.mypy.overrides]] module = "tests.*"` block.
- **pytest:** `pythonpath = ["."]`, `testpaths = ["tests"]`,
  `addopts = "--import-mode=importlib --cov"`.
- **coverage:** `[tool.coverage.run] source = ["core","data","backtest","llm",
  "execution","app"]`; no `--cov-fail-under` in Phase 0 (nothing to cover yet).

### 5. pre-commit (`.pre-commit-config.yaml`)
- ruff (lint + format).
- mypy as a `local` hook invoked through `uv run` (so it sees project deps —
  `mirrors-mypy` runs in an isolated env without pydantic and would emit
  spurious missing-import errors). Set `pass_filenames: false` and `args: []`
  so mypy reads its config + `files` from `pyproject.toml` rather than being
  handed per-file paths. Contributors must `uv sync` before running hooks.
- Hygiene hooks: trailing-whitespace, end-of-file-fixer, check-yaml,
  check-added-large-files.
- Secret scanning: **detect-private-key** plus **gitleaks** — detect-private-key
  only catches PEM headers and would miss a raw hex Ethereum key or API token,
  which is this project's actual risk surface. A committed `.env.example`
  documents required vars without real values.
- Full `pytest` stays in CI, not pre-commit.

### 6. CI — `.github/workflows/ci.yml`
- Triggers on push + pull_request; runs on `ubuntu-latest`.
- Steps: checkout → `astral-sh/setup-uv` with `enable-cache: true` and
  `cache-dependency-glob: "uv.lock"` → `uv python install 3.12` (CI allows
  downloads; no separate `actions/setup-python`) → `uv sync --frozen` →
  `uv run ruff check` → `uv run ruff format --check` → `uv run mypy` →
  `uv run pytest`.
- **Known gap (accepted):** CI provisions Python via uv, not the nix devshell,
  so CI ≠ the dev environment. This is the deliberate speed/parity trade from
  the Decisions section.

### 7. git + hygiene
- `git init`; `.gitignore` covering `.venv`, tool caches (`.pytest_cache`,
  `.mypy_cache`, `.ruff_cache`), `__pycache__`, `.direnv`, nix `result`, `.env`,
  and coverage artifacts.
- `.editorconfig` (global AGENTS.md expects agents to conform to one): UTF-8,
  LF, final newline, trim trailing whitespace, 4-space Python indent.
- `LICENSE`: MIT (standard text); `license = "MIT"` set in `pyproject.toml`.
- Minimal `README.md`: how to enter the devshell (direnv / `nix develop`), `uv`
  commands, and how to run the gates.
- No automatic commit — the initial commit is made only on explicit user
  instruction.

## Implementation ordering (bootstrap)

`uv sync --frozen` and a committed `uv.lock` create a chicken-and-egg: the lock
must exist before CI runs `--frozen`, but it cannot exist before the project
does. Sequence:

1. `git init`; write `flake.nix`; enter `nix develop` (provides python312 + uv).
2. Write `pyproject.toml` (deps + tool config), the skeleton, and the smoke test.
3. `uv lock` then `uv sync` to generate `uv.lock` and the venv.
4. Run the gates locally until green (`ruff check`, `ruff format --check`,
   `mypy`, `pytest`).
5. `pre-commit install`, then run it once across the whole repo.
6. Add the CI workflow.
7. Stage explicitly and commit **only on user instruction**, including
   `uv.lock`.

Any later dependency change must be followed by `uv lock` before commit, or CI's
`--frozen` will (correctly) fail.

## Acceptance gate

Inside `nix develop`:
- `uv run ruff check` clean
- `uv run ruff format --check` clean
- `uv run mypy` clean
- `uv run pytest --cov` green (the smoke test)
- CI workflow encodes the same gates and is green on push.

Matches `PLAN.md`: "CI green on an empty suite; lint and types clean."

## Out of scope (Phase 0)

- Any domain/financial logic (Phase 1+).
- Domain dependencies beyond `pydantic` (added per phase, on first use).
- Network adapters, LLM layer, execution, backtest harness.
