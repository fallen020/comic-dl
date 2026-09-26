# Development Setup

How to take a fresh machine to a working comic-dl checkout that passes the
gates. Everything here is also run by CI, so a checkout that follows these
commands behaves the same locally and on the runners.

## 1. Prerequisites

- **Python 3.11+** — any 3.11-3.14 is fine; CI tests the whole range.
- **uv** — the package manager. Never mix in `pip`/`venv`; the project is
  uv-managed and the `uv.lock` drives CI's `--locked` installs.
- **Git**.
- **GPG** (optional but expected for maintainers) — commits are signed.

Check versions:

```bash
python3 --version
uv --version
git --version
```

## 2. Clone and install

```bash
git clone https://github.com/fallen020/comic-dl
cd comic-dl
uv sync --extra dev --locked
```

What this does:

- `uv sync` creates a virtual environment and installs the package plus the
  `dev` extra (pytest, ruff, mypy, bandit, coverage).
- `--locked` insists the environment matches `uv.lock` exactly. If the command
  fails with a "lockfile is out of date" error, `uv.lock` changed on the
  branch — run `uv lock` (or `uv sync` without `--locked` if you know the
  lockfile is stale). Do not commit a hand-edited lockfile.

A bare `uv run` auto-syncs in the background and can prune dev packages. Prefer
`uv sync --extra dev --locked` when something is missing after a branch switch.

## 3. Verify the install

```bash
uv run comic-dl self version
uv run python -m comic_dl --help
```

The version must match `pyproject.toml`. If `comic-dl` is not found but
`python -m comic_dl` works, the console-script entry point is the problem — the
checkout is on an old `dev` head; pull and re-sync.

## 4. Run the gates once

CI runs these scripts verbatim. Prove the environment before writing code:

```bash
./scripts/test.sh
./scripts/lint.sh
./scripts/docs.sh
```

| Script | Purpose |
| :----- | :------ |
| `test.sh` | Runs `pytest tests/` in parallel, cache disabled |
| `lint.sh` | ruff check + format-check, mypy, bandit on `src/`, ShellCheck |
| `docs.sh` | site/manifest/mirror drift + pymarkdown scan of `docs/` + `README.md` |
| `build.sh` | Builds sdist + wheel and repacks `_version.py` |
| `build-binary.sh` | PyInstaller one-file binary (`.ps1` on Windows) |

First run of `lint.sh` may report `shellcheck` not found — it is optional and
skipped. First run of `test.sh` hits the download of test fixtures; afterwards
the suite is fully offline.

## 5. What each gate catches

- **test.sh** — behaviour. Offline-only; no live network calls anywhere.
- **ruff** — style, unused imports, common bugs, and *docstring presence*
  (`D100`-`D104`, `D106`). The project intentionally ignores the style content
  of docstrings (`D200`-`D419`) and method-level presence (`D102`, `D105`,
  `D107`).
- **mypy** — type soundness across the source tree.
- **bandit** — known insecure patterns. If it flags a line, prefer fixing the
  call (`sha256` over `md5` for identity hashes) over a `# nosec`.
- **docs.sh** — table drift (sites), dead links in both doc trees, `{#...}`
  heading IDs that would render literally on GitHub, and Markdown lint.
- **build.sh** — packaging produces a runnable artifact.

## 6. Editor and daily loop

The repo is plain Python + Markdown; ruff config lives in `pyproject.toml`, so
any editor with ruff/mypy support picks the project settings up automatically.
Run `ruff format` against your changes before pushing — `lint.sh` checks
formatting, and `--fix` mode (`./scripts/lint.sh --fix`) fixes what it can.

The loop you will actually live in:

```bash
uv run pytest tests/<file>.py -q   # focused test while editing
./scripts/test.sh                  # full suite
./scripts/lint.sh                  # before pushing
./scripts/docs.sh                  # if docs changed (or every time; it is fast)
```

## 7. Troubleshooting the setup

| Symptom | Cause/fix |
| :------ | :-------- |
| `uv run` fails on import | stale sync: `uv sync --extra dev --locked` |
| tests import errors on `pytest` | the `dev` extra is missing — see above |
| `ModuleNotFoundError: comic_dl` | outside the `uv sync` venv, or plain import instead of package |
| lockfile error | `uv.lock` ahead: `git pull --rebase`, then `uv sync --locked` |
| `bandit` false positive | document it in the PR; no scattergun `# nosec BXXX` (misleads audits) |
| weird Windows test behavior | rely on CI for Windows; Linux local dev is representative |

## 8. Setting up commit signing (maintainers)

`main` requires signed commits:

```bash
git config --global user.signingkey <YOUR-KEY-ID>
git config --global commit.gpgsign true
```

The GPG key `D784B9E3D5FA85D2` is the release key; contributor keys are their
own. After a rebase, re-sign (`git commit --amend -S --no-edit`) before
pushing.
