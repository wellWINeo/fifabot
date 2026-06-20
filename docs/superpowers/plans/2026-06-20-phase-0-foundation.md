# Phase 0 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a reproducible, nix-managed Python repo where the quality gate (`ruff` + `mypy` + `pytest`) is enforced by machinery (pre-commit + CI), over the flat module skeleton from `CLAUDE.md`.

**Architecture:** A Nix flake devshell pins Python 3.12 + uv; uv manages deps/venv inside the nix interpreter. The repo is a non-packaged uv project (`package = false`) of flat top-level modules. Quality config lives in `pyproject.toml`; pre-commit enforces it locally and a GitHub Actions workflow re-runs the same gates via uv (not nix) in CI.

**Tech Stack:** Nix flakes, direnv, uv, Python 3.12, pydantic v2, pytest + pytest-cov, hypothesis, ruff, mypy, pre-commit, gitleaks, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-06-20-phase-0-foundation-design.md`

## Global Constraints

These apply to every task:

- **Python:** `requires-python = ">=3.12"`; pinned to 3.12 via nix + `.python-version`.
- **uv project:** `[tool.uv] package = false` (non-distributable app of flat modules).
- **Runtime deps (now):** `pydantic>=2` only. **Dev deps (now):** `pytest`, `pytest-cov`, `hypothesis`, `ruff`, `mypy`, `pre-commit`. No other domain deps (numpy/scipy/etc.) in Phase 0.
- **Layout:** flat top-level modules `core/ data/ backtest/ llm/ execution/ app/` at repo root; tests under `tests/`.
- **Lock discipline:** `uv.lock` is committed; CI runs `uv sync --frozen`. Any dependency change must be followed by `uv lock` before commit, or CI fails (correctly).
- **Secrets:** never commit `.env`, keys, or tokens. `detect-private-key` + `gitleaks` guard this; `.env.example` documents vars without values.
- **Attribution:** commit messages carry NO `Co-Authored-By` / "Generated with" trailers (per `AGENTS.md`).

## Git & commit protocol (read before Task 1)

- `git init` defaults to branch `master` here (`init.defaultBranch` is unset). Per `AGENTS.md`, do **not** commit to `master`. Immediately after `git init`, **confirm with the user**: feature branch in place vs. a worktree. Default branch name: `phase-0-foundation`. All Phase 0 commits land on that branch.
- **Commit only on explicit user instruction.** The commit steps below are written out, but the executor runs them only once the user has authorized committing.
- Stage files **explicitly by path** — never `git add -A` / `git add .`.
- Do not push unless asked.

---

### Task 1: Nix devshell + git init + .gitignore

**Files:**
- Create: `flake.nix`
- Create: `.envrc`
- Create: `.python-version`
- Create: `.gitignore`

**Interfaces:**
- Produces: a working `nix develop` shell exposing `python3.12` and `uv`, with uv pinned to the nix interpreter. All later tasks run their commands inside this shell.

- [ ] **Step 1: `git init` and confirm branch**

```bash
git init
git checkout -b phase-0-foundation   # only after confirming branch strategy with the user
```
Expected: `Initialized empty Git repository`; now on `phase-0-foundation`.

- [ ] **Step 2: Write `.gitignore`** (first, so generated caches are never staged)

```gitignore
# Python
__pycache__/
*.py[cod]
.venv/

# Tooling caches
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
coverage.xml
htmlcov/

# Nix / direnv
result
result-*
.direnv/

# Secrets / env
.env
.env.*
!.env.example

# OS / editor
.DS_Store
```

- [ ] **Step 3: Write `flake.nix`**

```nix
{
  description = "fifabot — Polymarket soccer trading research";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "aarch64-darwin" "x86_64-darwin" "x86_64-linux" "aarch64-linux" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f system);
    in
    {
      devShells = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          python = pkgs.python312;
        in
        {
          default = pkgs.mkShell {
            packages = [ python pkgs.uv ];
            env = {
              UV_PYTHON = "${python}/bin/python3.12";
              UV_PYTHON_PREFERENCE = "only-system";
              UV_PYTHON_DOWNLOADS = "never";
            };
            shellHook = ''
              echo "fifabot devshell — $(python --version)"
            '';
          };
        });
    };
}
```

- [ ] **Step 4: Write `.python-version` and `.envrc`**

`.python-version`:
```
3.12
```

`.envrc`:
```
use flake
```

- [ ] **Step 5: Verify the devshell**

Run:
```bash
nix develop --command python --version
nix develop --command uv --version
```
Expected: `Python 3.12.x`; a `uv 0.x.x` line. (First run generates `flake.lock` and downloads the toolchain — may take a few minutes.)

- [ ] **Step 6: Allow direnv (optional, for auto-activation)**

Run: `direnv allow`
Expected: subsequent `cd` into the repo loads the devshell automatically.

- [ ] **Step 7: Commit**

```bash
git add flake.nix flake.lock .envrc .python-version .gitignore
git commit -m "build: nix flake devshell pinning python 3.12 + uv"
```

---

### Task 2: uv project + module skeleton + smoke test

**Files:**
- Create: `pyproject.toml`
- Create: `core/__init__.py`, `data/__init__.py`, `backtest/__init__.py`, `llm/__init__.py`, `execution/__init__.py`, `app/__init__.py`
- Create: `tests/__init__.py`, `tests/test_skeleton.py`
- Create (generated): `uv.lock`

**Interfaces:**
- Consumes: the devshell from Task 1.
- Produces: six importable top-level packages; a green `uv run pytest`; a committed `uv.lock`.

- [ ] **Step 1: Write the failing test first**

`tests/test_skeleton.py`:
```python
"""Smoke test: every top-level package imports cleanly."""

import importlib

PACKAGES = ("core", "data", "backtest", "llm", "execution", "app")


def test_packages_import() -> None:
    for name in PACKAGES:
        assert importlib.import_module(name) is not None
```

`tests/__init__.py`:
```python
```
(empty file)

- [ ] **Step 2: Write `pyproject.toml`** (deps + pytest/coverage config; ruff & mypy added in later tasks)

```toml
[project]
name = "fifabot"
version = "0.0.0"
description = "Automated trading research for Polymarket soccer markets"
requires-python = ">=3.12"
license = "MIT"
dependencies = [
    "pydantic>=2",
]

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-cov>=5",
    "hypothesis>=6",
    "ruff>=0.6",
    "mypy>=1.11",
    "pre-commit>=3.8",
]

[tool.uv]
package = false

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
addopts = "--import-mode=importlib --cov"

[tool.coverage.run]
source = ["core", "data", "backtest", "llm", "execution", "app"]
```

- [ ] **Step 3: Create the six package directories**

```bash
nix develop --command bash -c 'for p in core data backtest llm execution app; do mkdir -p "$p"; printf "" > "$p/__init__.py"; done'
```
Expected: six dirs each with an empty `__init__.py`.

- [ ] **Step 4: Generate the lockfile and venv**

Run:
```bash
nix develop --command uv lock
nix develop --command uv sync
```
Expected: `uv.lock` created; `.venv/` populated with pydantic + dev tools.

- [ ] **Step 5: Run the smoke test — verify it passes**

Run: `nix develop --command uv run pytest -v`
Expected: `tests/test_skeleton.py::test_packages_import PASSED`, `1 passed`. (A short coverage summary prints; no failure.)

- [ ] **Step 6: Sanity-check it would fail without the skeleton (optional confidence check)**

Run: `nix develop --command uv run python -c "import importlib; importlib.import_module('nonexistent_pkg')"`
Expected: `ModuleNotFoundError` — confirms the import mechanism the test relies on actually raises on a missing package.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock core data backtest llm execution app tests
git commit -m "build: uv project, flat module skeleton, import smoke test"
```

---

### Task 3: ruff lint + format config, repo clean

**Files:**
- Modify: `pyproject.toml` (add `[tool.ruff]` sections)

**Interfaces:**
- Consumes: the project from Task 2.
- Produces: `uv run ruff check` and `uv run ruff format --check` both clean.

- [ ] **Step 1: Add ruff config to `pyproject.toml`**

Append:
```toml
[tool.ruff]
target-version = "py312"
line-length = 88

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.ruff.lint.isort]
known-first-party = ["core", "data", "backtest", "llm", "execution", "app"]
```

- [ ] **Step 2: Run the linter — expect it to gate**

Run: `nix develop --command uv run ruff check`
Expected: `All checks passed!` (if any issues surface, fix the flagged lines, then re-run until clean).

- [ ] **Step 3: Run the formatter check**

Run: `nix develop --command uv run ruff format --check`
Expected: `N files already formatted`. If it reports files needing formatting, run `uv run ruff format` then re-run `--check`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "build: ruff lint + format config"
```

---

### Task 4: mypy strict config, repo clean

**Files:**
- Modify: `pyproject.toml` (add `[tool.mypy]`)

**Interfaces:**
- Consumes: the project from Tasks 2–3.
- Produces: `uv run mypy` clean under strict mode, with flat-layout resolution configured.

- [ ] **Step 1: Add mypy config to `pyproject.toml`**

Append:
```toml
[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]
files = ["core", "data", "backtest", "llm", "execution", "app", "tests"]
mypy_path = "."
explicit_package_bases = true
namespace_packages = true
```

- [ ] **Step 2: Run mypy — verify clean**

Run: `nix develop --command uv run mypy`
Expected: `Success: no issues found in N source files`.
If it reports "duplicate module" or "cannot find implementation", confirm `explicit_package_bases`/`namespace_packages` are set as above and that each module dir has an `__init__.py`.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "build: mypy strict config with flat-layout resolution"
```

---

### Task 5: pre-commit hooks + .env.example

**Files:**
- Create: `.pre-commit-config.yaml`
- Create: `.env.example`

**Interfaces:**
- Consumes: ruff/mypy config from Tasks 3–4 (the local hooks invoke them via `uv run`).
- Produces: `pre-commit run --all-files` green; commit-time enforcement of ruff, mypy, hygiene, and secret scanning.

- [ ] **Step 1: Write `.env.example`** (documents required vars; real values go in untracked `.env`)

```bash
# Copy to `.env` and fill in. NEVER commit `.env`.
# Vars are added as later phases need them.

# Wallet / signing (execution phase)
PRIVATE_KEY=

# Polymarket CLOB API (execution phase)
CLOB_API_KEY=
CLOB_API_SECRET=
CLOB_API_PASSPHRASE=
```

- [ ] **Step 2: Write `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-toml
      - id: check-added-large-files
      - id: detect-private-key

  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.18.4
    hooks:
      - id: gitleaks

  - repo: local
    hooks:
      - id: ruff-check
        name: ruff check
        entry: uv run ruff check --fix
        language: system
        types: [python]
        require_serial: true
      - id: ruff-format
        name: ruff format
        entry: uv run ruff format
        language: system
        types: [python]
        require_serial: true
      - id: mypy
        name: mypy
        entry: uv run mypy
        language: system
        pass_filenames: false
```

- [ ] **Step 3: Pin hook revs to current**

Run: `nix develop --command uv run pre-commit autoupdate`
Expected: updates `rev:` for `pre-commit-hooks` and `gitleaks` to the latest tags (local hooks are unaffected). This avoids relying on the baseline revs above being current.

- [ ] **Step 4: Install and run all hooks**

Run:
```bash
nix develop --command uv run pre-commit install
nix develop --command uv run pre-commit run --all-files
```
Expected: every hook reports `Passed` (or auto-fixes whitespace/EOF on first run — if so, re-run until all `Passed`).

- [ ] **Step 5: Commit**

```bash
git add .pre-commit-config.yaml .env.example
git commit -m "build: pre-commit hooks (ruff, mypy, gitleaks) + .env.example"
```

---

### Task 6: Project metadata & docs

**Files:**
- Create: `.editorconfig`
- Create: `LICENSE`
- Create: `README.md`

**Interfaces:**
- Consumes: dev workflow established in Tasks 1–5 (README documents it).
- Produces: editor consistency, MIT license, and a minimal dev-setup README; pre-commit still green.

- [ ] **Step 1: Write `.editorconfig`**

```ini
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true
indent_style = space

[*.py]
indent_size = 4

[*.{yml,yaml,toml,nix}]
indent_size = 2

[*.md]
trim_trailing_whitespace = false
```

- [ ] **Step 2: Write `LICENSE`** (MIT, holder = configured git identity)

```
MIT License

Copyright (c) 2026 wellWINeo

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 3: Write `README.md`**

````markdown
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
````

- [ ] **Step 4: Verify gates still green**

Run: `nix develop --command uv run pre-commit run --all-files`
Expected: all hooks `Passed` (this also lints/formats the new files).

- [ ] **Step 5: Commit**

```bash
git add .editorconfig LICENSE README.md
git commit -m "docs: editorconfig, MIT license, dev-setup README"
```

---

### Task 7: GitHub Actions CI

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `uv.lock` and tool config from Tasks 2–4.
- Produces: a workflow that re-runs the full gate via uv on push/PR (active once a GitHub remote exists).

- [ ] **Step 1: Write `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
  pull_request:

jobs:
  gates:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v6
        with:
          enable-cache: true
          cache-dependency-glob: "uv.lock"

      - name: Install Python 3.12
        run: uv python install 3.12

      - name: Sync dependencies
        run: uv sync --frozen

      - name: Ruff lint
        run: uv run ruff check

      - name: Ruff format check
        run: uv run ruff format --check

      - name: Mypy
        run: uv run mypy

      - name: Pytest
        run: uv run pytest
```

- [ ] **Step 2: Validate the workflow YAML locally**

Run: `nix develop --command uv run pre-commit run check-yaml --files .github/workflows/ci.yml`
Expected: `check-yaml ... Passed`. (Full CI execution is verified on first push to a GitHub remote — out of scope for Phase 0, which has no remote yet.)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: GitHub Actions gate (ruff, mypy, pytest via uv)"
```

---

## Final acceptance check (Phase 0 gate)

Run all gates fresh inside the devshell:

```bash
nix develop --command bash -c '
  uv sync --frozen &&
  uv run ruff check &&
  uv run ruff format --check &&
  uv run mypy &&
  uv run pytest &&
  uv run pre-commit run --all-files
'
```
Expected: every command exits 0 — ruff clean, format clean, mypy `Success`, pytest `1 passed`, all pre-commit hooks `Passed`.

Then mark Phase 0 complete in `PLAN.md`:
- [ ] Change `- [ ] Phase 0 — Foundation` to `- [x] Phase 0 — Foundation`, commit with `docs: mark Phase 0 complete`.

This satisfies `PLAN.md`'s Phase 0 gate: "CI green on an empty suite; lint and types clean."
