# Development Setup

How to set up a development environment for contributing to comic-dl.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) package manager
- Git

## Clone and install

```bash
git clone https://github.com/fallen020/comic-dl
cd comic-dl
uv sync --extra dev --locked
```

This installs all runtime and development dependencies in a virtual environment.

## Scripts

| Script | Command | Description |
| :----- | :------ | :---------- |
| Test | `./scripts/test.sh` | Run the test suite |
| Test with coverage | `./scripts/test.sh --cov=comic_dl` | Run with coverage report |
| Lint + type check | `./scripts/lint.sh` | ruff + mypy + bandit on `src/comic_dl/` |
| Build package | `./scripts/build.sh` | Build sdist + wheel |
| Build binary | `./scripts/build-binary.sh` | PyInstaller one-file binary (Windows: `.ps1`) |

You can also run these directly with `uv`:

```bash
uv run pytest tests/ -q               # run tests
uv run ruff check src/comic_dl/       # lint
uv run mypy src/comic_dl/             # type check
uv run bandit -c pyproject.toml src/comic_dl/  # security scan
```

## Running from source

```bash
uv run python -m comic_dl --help
uv run python -m comic_dl -u <URL>
```

## Testing approach

All tests are **offline-only** — no live network calls. Tests use mocked HTTP
responses and local fixtures. CI is deterministic without network access.

```bash
uv run pytest tests/ -q                    # all tests
uv run pytest tests/test_downloader.py -q  # single file
uv run pytest tests/security/ -q           # security tests only
```

See [Testing](testing.md) for details.

## Documentation

The docs are plain Markdown under `docs/`.

```bash
uv run --extra docs pymarkdown scan docs README.md  # lint markdown
```

The API reference in `docs/api-reference.md` is maintained by hand — keep
docstrings current when changing public contracts.

## Code style

- Google-style docstrings (one-line summaries for modules and public functions).
- Comments explain *why*, not *what*.
- Conventional Commits: `feat:`, `fix:`, `docs:`, `chore:`, `perf:`,
  `refactor:`, `test:`.
- One concern per small commit.

## Branching

- Development happens on feature branches merged to `main` via reviewed PRs.
- CI runs tests, ruff, mypy, bandit, and `pip-audit` on every push/PR.
- Heavier packaging is reserved for tagged releases.
