# AGENTS.md

## Project

`comic-dl` downloads comic/manga galleries from supported sites and compiles
them into CBZ, ZIP, and CBT archives. Latest release (`v0.0.2`).
See `docs/develop/releasing.md` for the runbook.

Upstream: `https://github.com/fallen020/comic-dl`, default branch `main`.
Remote uses SSH (`git@github.com:fallen020/comic-dl.git`); HTTPS needs a
PAT via the credential helper (password auth is dead).

Three long-lived branches: `dev` (unstable, work lands here), `staging`
(validation, mirrors what will ship), `main` (production, releases only).
Feature branches fork from `dev` and merge back via squash PRs.

## Stack

- Python >=3.11, managed with `uv`; package name `comic-dl`.
- `uv sync --extra dev --locked` to set up. Never mix `pip`/`venv`.

## Commands (run after every change)

| Gate | Command |
| :--- | :------ |
| Lint | `./scripts/lint.sh` |
| Test | `./scripts/test.sh` — single file: `uv run pytest tests/test_X.py -q` |
| Build | `./scripts/build.sh` |
| Docs | `./scripts/docs.sh` — runs `update-sites-docs.py --check`, then Markdown lint |
| Docs tables | `uv run python scripts/update-sites-docs.py` (after adding a site) |

## Repository map

```
src/comic_dl/
  __init__.py, __main__.py, _version.py
  cli/__init__.py          # CLI orchestration (large — read surrounding context first)
  cli/library.py, plugins.py, selection.py, sizing.py
  scrapers/
    base.py                # BaseScraper contract
    generic.py             # Fallback HTML scraper
    madara.py              # Madara-theme framework scraper
    registry.py            # Plugin loader
    refresh.py             # Chapter re-fetch logic
    sites/                 # Per-site parsers (21 built-in, auto-discovered)
  archiver.py              # CBZ/ZIP/CBT packing
  downloader.py            # Async download engine
  comicinfo.py             # ComicInfo.xml generation
  config.py                # TOML config parsing
  cache.py                 # Scrape response cache
  rate.py                  # Per-host token-bucket limiter
  http.py, cookies.py      # HTTP client, cookie jar
  cf.py, antibot.py        # Cloudflare detection, WAF fingerprints
  webview.py, webview_solver.py, webview_constants.py  # System-webview solver
  library.py               # SQLite download history (library CLI + update)
  self_update.py           # `self` — install-source detection + update strategies
  ui.py                    # Rich progress/rendering (large)
  models.py, errors.py, utils.py, platform.py
tests/                     # Offline-safe suite (no live network)
  security/                # SSRF and filesystem safety tests
  scrapers/sites/          # Per-site parser tests
docs/                      # Plain Markdown docs, source of truth (author here,
                           # then mirror published pages into website/ as .mdx)
scripts/                   # CI gate scripts + docs generator (update-sites-docs.py)
packaging/                 # Distro packaging (deb/rpm/arch), versioning, PyInstaller
examples/                  # Sample config, plugin, URL list
website/                   # Astro docs site (GitHub Pages); mirrors docs/ subset
```

Note: `scripts/update-sites-docs.py` regenerates the supported-sites tables in
both `docs/reference/` and `website/src/content/docs/reference/`. Run
`docs.sh` (via `update-sites-docs.py --check`) after adding or changing a site.

## Non-obvious conventions

- **Security:** every outbound fetch passes `validate_request_url` via
  `BaseScraper._timeout_get` / `_open_stream`. Never weaken it.
- **Bandit nosec:** scoped `# nosec BXXX` triggers a spurious bandit warning
  whenever the marked line also falls inside an enclosing AST statement
  (bandit 1.9.4). Prefer fixing the flagged call (e.g. `sha256` over `md5`
  for identity hashes) or a plain `# nosec` plus a WHY comment.
- **Politeness:** per-host rate limiter (`rate.py`) and shared retry cooldown
  are load-bearing. Never bypass them.
- **Errors:** 0 success / 1 error / 2 usage / 130 interrupted (`errors.py`).
  User-facing text through `ui.py` helpers; never leak raw exception args.
- **Comments:** Google-style docstrings; WHY-not-WHAT; no filler.
  Never add a comment that restates code. Write about *why*, not *what*, unless
  the behavior is genuinely non-obvious — that includes not narrating control
  flow or function purpose in comments. No process narration ("we need to",
  "this ensures"); no praise, summaries, or AI/ChatGPT mentions; no TODO/FIXME
  without a concrete, actionable issue. Prefer self-explanatory code; if an
  explanation needs several paragraphs, put it in `docs/`, not a comment.
  When editing code, only update comments if the behavior or rationale they
  describe has changed.
- **Docs:** `docs/` is the single source of truth; `README.md` is a landing page.
  When docs name a CLI surface (columns, flags, subcommands), verify the claim
  against `src/` first. Never describe aspirational capabilities as present —
  say the exact output (e.g. SOURCE · ORIGIN columns) or leave it out.
- **Tests:** offline-only, must stay that way for deterministic CI.
- **Tests:** assert on unwrapped content — Rich folds console output at 80
  cols on CI, splitting paths/phrases mid-token. Compare against
  `output.replace("\n", "")`, never raw capture. Machine JSON must print
  with `soft_wrap=True` so narrow terminals cannot corrupt it.
- **Tests:** never construct `Path()` while `os.name` is mocked — on
  Python < 3.12 it dispatches to `WindowsPath` and raises on POSIX (and
  crashes pytest's own failure renderer the same way).

## Git workflow

- Conventional Commits: `feat:` `fix:` `docs:` `chore:` `perf:` `refactor:` `test:`
- **Branch flow:** feature branches fork from `dev` and land there via squash
  PRs. `dev` is unstable — integration and experiments happen here. When work
  is release-ready, `staging` is cut from `dev` and validated (CI, packaging,
  release smoke). `staging` proves what will ship without blocking `dev`.
- `main` is protected: signed commits required, linear history, no force
  pushes or deletions. Land work via squash-merges (one commit per PR).
  Every Monday, the validated `staging` state is released to `main` as a new
  `vX.Y.Z` (Monday cadence in `docs/develop/releasing.md`).
- After `git pull --rebase`, re-sign if the signature was dropped, then push.
- Tags are GPG-signed and must equal `version` in `pyproject.toml`
  exactly (PEP 440, e.g. `v0.0.1` — never `v0.0.1-beta`; hyphens break
  arch/rpm versioning and the release guards). Pushing a `v*` tag triggers
  `release.yml`. Moving a tag is allowed only to repair an unpublished
  broken release.
- Dependabot edits `pyproject.toml` but not `uv.lock`: after merging any
  dependency PR, run `uv lock` against the merged tree and push the
  refreshed lockfile, or every `--locked` gate fails.
- Releasing: `docs/develop/releasing.md`. Pushing, tagging, and PyPI publishes
  are boundary actions — always confirm first.

## Boundaries

- **Always:** run gates after changes; add tests for behavior changes; search
  existing patterns before adding abstractions.
- **Ask first:** raising request rates, weakening validation, committing/pushing,
  changing dependencies in `pyproject.toml`.
- **Never:** commit secrets or `.env` files; modify `.agents/` or `skills-lock.json`.

## Key docs

| Topic | Read |
| :---- | :--- |
| Usage, flags, config | `docs/reference/cli.md`, `docs/usage/download.md`, `docs/configure/config.md` |
| Library CLI | `docs/usage/library.md` |
| Self-update | `docs/usage/self-update.md` |
| Supported sites | `docs/reference/supported-sites.md` |
| Writing a scraper | `docs/usage/write-plugin.md`, `examples/plugin-example/` |
| Architecture | `docs/develop/architecture.md` |
| Releasing | `docs/develop/releasing.md` |
| Error style | `docs/develop/error-style-guide.md` |
| Security testing | `docs/develop/security-testing.md` |
