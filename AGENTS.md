# AGENTS.md

`comic-dl` downloads comic/manga galleries from supported sites and compiles
them into CBZ, ZIP, and CBT archives. Upstream: `fallen020/comic-dl`
(default branch `main`). Latest release: `CHANGELOG.md`; release runbook:
`docs/develop/releasing.md`.

## Setup and gates

Python >=3.11, managed with `uv` (package `comic-dl`). Set up with
`uv sync --extra dev --locked`. Never mix `pip`/`venv`.

Run every gate after every change:

| Gate  | Command |
| :---- | :------ |
| Lint  | `./scripts/lint.sh` |
| Test  | `./scripts/test.sh` (single file: `uv run pytest tests/test_X.py`) |
| Build | `./scripts/build.sh` |
| Docs  | `./scripts/docs.sh` (site-table, manifest, and mirror `--check`s, then Markdown lint) |

Adding or changing a site:

1. Scaffold: `uv run python scripts/new-site.py NAME DOMAIN [--theme THEME]`
2. Regenerate the supported-sites tables (`docs/reference/` and
   `website/src/content/docs/reference/`):
   `uv run python scripts/update-sites-docs.py`

## Repository map

```
src/comic_dl/
  cli/                 # CLI orchestration; cli/__init__.py is large, read
                       # surrounding context first
  scrapers/
    base.py            # BaseScraper contract
    generic.py         # Fallback HTML scraper
    madara.py          # Madara-theme framework scraper
    registry.py        # Plugin loader
    refresh.py         # Chapter re-fetch logic
    sites/             # Per-site parsers, auto-discovered (count in
                       # docs/reference/supported-sites.md)
  archiver.py          # CBZ/ZIP/CBT packing
  downloader.py        # Async download engine
  comicinfo.py         # ComicInfo.xml generation
  config.py            # TOML config parsing
  cache.py             # Scrape response cache
  rate.py              # Per-host token-bucket limiter
  http.py, cookies.py  # HTTP client, cookie jar
  cf.py, antibot.py    # Cloudflare detection, WAF fingerprints
  webview*.py          # System-webview solver
  library.py           # SQLite download history (library CLI + update)
  self_update.py       # `self`: install-source detection + update strategies
  site_update.py       # `self site`: per-site versions, checks, live-check
  ui.py                # Rich progress/rendering (large)
  models.py, errors.py, utils.py, platform.py
tests/                 # Offline-safe suite (no live network)
  security/            # SSRF and filesystem safety
  scrapers/sites/      # Per-site parser tests
docs/                  # Source of truth (plain Markdown); mirror published
                       # pages into website/ as .mdx
scripts/               # Gates, docs generator, manifest/mirror checkers,
                       # scraper scaffold
packaging/             # Distro packaging (deb/rpm/arch), versioning, PyInstaller
examples/              # Sample config, plugin, URL list
website/               # Astro docs site (GitHub Pages); mirrors a docs/ subset
```

## Website (`website/`)

Astro 7 static docs site (MDX, Tailwind 4, Shiki, Pagefind search),
published to GitHub Pages by `.github/workflows/docs-deploy.yml` on `main`
(`https://fallen020.github.io/comic-dl`).

- Commands (run from `website/`): `npm run dev`, `npm run build`, `npm run preview`.
  Use `npm run build:search` before `preview`. Plain `build` skips the Pagefind index.
- Content is a hand mirror of `docs/`: author in `docs/`, copy to
  `website/src/content/docs/` as `.mdx`. `docs.sh` enforces the mirror.

## Conventions

- **Security:** every outbound fetch must pass `validate_request_url` via
  `BaseScraper._timeout_get` / `_open_stream`. Never weaken it.
- **Politeness:** the per-host rate limiter (`rate.py`) and shared retry
  cooldown are load-bearing. Never bypass them.
- **Errors:** exit codes are 0 success / 1 error / 2 usage / 130 interrupted
  (`errors.py`). Route user-facing text through `ui.py` helpers; never leak
  raw exception args.
- **Comments:** Google-style docstrings; explain WHY, not WHAT. No filler,
  code restatement, process narration ("we need to", "this ensures"), praise,
  or AI mentions. No TODO/FIXME without a concrete, actionable issue.
  Anything longer than a paragraph belongs in `docs/`. Touch a comment only
  when the behavior it describes changes.
- **Bandit:** with bandit 1.9.4, a scoped `# nosec BXXX` raises a spurious
  warning when the marked line sits inside an enclosing AST statement. Prefer
  fixing the flagged call (e.g. `sha256` instead of `md5` for identity
  hashes); otherwise use a plain `# nosec` with a WHY comment.
- **Docs:** `docs/` is the single source of truth; `README.md` is a landing
  page. Verify every CLI surface named in docs (columns, flags, subcommands)
  against `src/` first. Never present aspirational behavior as shipped.
- **Tests:** offline only, so CI stays deterministic.
  - Rich folds console output at 80 columns on CI: compare against
    `output.replace("\n", "")` and print machine JSON with `soft_wrap=True`.
  - Never construct `Path()` while `os.name` is mocked; on Python < 3.12 it
    dispatches to `WindowsPath` and raises on POSIX.

## Git workflow

- **Branches:** feature branches fork from `dev` (unstable) and merge back via
  squash PRs. Release-ready work is cut to `staging` for validation (CI,
  packaging, release smoke), then released to `main` every Monday as a new
  `vX.Y.Z`.
- **`main`:** protected. Signed commits, linear history, no force pushes or
  deletions.
- **Remote:** SSH (`git@github.com:fallen020/comic-dl.git`). HTTPS needs a PAT
  via the credential helper.
- **Commits:** Conventional Commits (`feat:` `fix:` `docs:` `chore:` `perf:`
  `refactor:` `test:`). After `git pull --rebase`, re-sign if the signature
  was dropped, then push.
- **Tags:** GPG-signed and equal to `version` in `pyproject.toml` exactly
  (PEP 440, e.g. `v0.0.1`; never `v0.0.1-beta`, since hyphens break arch/rpm
  versioning and the release guards). Pushing a `v*` tag triggers
  `release.yml`. Move a tag only to repair an unpublished broken release.
- **Dependabot:** it edits `pyproject.toml` but not `uv.lock`. After merging
  any dependency PR, run `uv lock` on the merged tree and push the refreshed
  lockfile, or every `--locked` gate fails.

## Boundaries

- **Always:** run the gates after changes; add tests for behavior changes;
  search existing patterns before adding abstractions.
- **Ask first:** committing or pushing, tagging, publishing to PyPI, raising
  request rates, weakening validation, changing dependencies in
  `pyproject.toml`.
- **Never:** commit secrets or `.env` files; modify `.agents/` or
  `skills-lock.json`.

## Key docs

| Topic | Read |
| :---- | :--- |
| Usage, flags, config | `docs/reference/cli.md`, `docs/usage/download.md`, `docs/configure/config.md` |
| Library CLI | `docs/usage/library.md` |
| Self-update | `docs/usage/self-update.md` |
| Site support | `docs/usage/site-support.md`, `docs/reference/supported-sites.md` |
| Writing a scraper | `docs/usage/write-plugin.md`, `examples/plugin-example/` |
| Development | `docs/develop/index.md`, `docs/develop/workflow.md`, `docs/develop/setup.md`, `docs/develop/testing.md`, `docs/develop/code-review.md`, `docs/develop/reporting.md` |
| Architecture | `docs/develop/architecture.md` |
| API reference | `docs/api-reference.md` (hand-maintained; keep docstrings current) |
| Releasing | `docs/develop/releasing.md` |
| Error style | `docs/develop/error-style-guide.md` |
| Security testing | `docs/develop/security-testing.md` |
