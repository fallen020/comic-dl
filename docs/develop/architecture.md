# Architecture

An overview of comic-dl's internal design, data flow, and security model.

## Source layout

```
src/comic_dl/
  __main__.py            console entry point
  cli/
    __init__.py          argument parsing, URL routing, orchestration
    library.py           list/info/latest/remove subcommands
    selection.py         interactive chapter-selection prompt
    sizing.py            size caps, download-size estimates, disk-space checks
  config.py              config file + platform directory resolution
  platform.py            thin OS seam (system(), machine(), downloads_dir(), …)
  _version.py            generated version data (from pyproject.toml)
  utils.py               URL normalization, sanitization, SSRF guard,
                         image magic-byte verification
  models.py              core data contracts (ImageItem, PostMetadata,
                         ChapterInfo, SourceInfo, ScrapedChapter, SeriesMetadata)
  errors.py              error types and exit codes
  downloader.py          streaming/retry download engine + DownloadPipeline
  archiver.py            archive creation (.cbz/.zip/.cbt; atomic writes)
  comicinfo.py           ComicInfo.xml generator
  library.py             local SQLite cache of series/chapters
  ui.py                  rich console output (banner, progress, tables)
  http.py                shared curl_cffi session/request helpers
  cookies.py             persistent cookie jar (SQLite, RFC 6265)
  cache.py               scrape-response cache (TTL, ETag revalidation)
  rate.py                per-site request throttling (token bucket)
  cf.py                  Cloudflare challenge detection and solver routing
  host_registry.py       known-protected hosts → recommended solver modes
  antibot.py             challenge classification + browser-driver fallback
  webview.py             system-webview Cloudflare solver (bundled pywebview)
  webview_solver.py      headless Cloudflare-challenge solver subprocess
  webview_constants.py   shared tunables between solver and parent
  scrapers/
    __init__.py          stable source contract; importing registers built-ins
    registry.py          entry-point discovery, URL → scraper routing
    base.py              shared helpers (meta extraction, validated fetch)
    generic.py           yt-dlp-style fallback scraper
    madara.py            shared Madara-theme scraper framework
    refresh.py           stale-image refresh registration
    sites/
      __init__.py        imports every site module (registers them)
      webtoon.py         WEBTOON scraper
      ehentai.py         e-hentai scraper
      pawchive.py        pawchive scraper
      flamecomics.py     FlameComics scraper
      fsicomics.py       FSIComics scraper
      gedecomix.py       GEDE Comix scraper
      asurascans.py      Asura Scans scraper
      kagane.py          Kagane scraper
      mangadex.py        MangaDex scraper
       manhwaz.py         Manhwaz scraper
       toonily.py         Toonily scraper
       kodokustudio.py    KodokuStudio scraper
```

## Data flow

1. **Routing** — normalize the URL, resolve its domain, look up a scraper in
   `scrapers/registry.py`. If no domain scraper exists and the generic fallback
   is enabled, use `GenericScraper`.

2. **Metadata** — the scraper returns `PostMetadata` (chapter) or
   `SeriesMetadata` (series listing) through `BaseScraper.scrape()` /
   `BaseScraper.scrape_series()`.

3. **Safety** — every outbound URL passes `validate_request_url`. Redirect hops
   are re-validated on both the scrape and download paths.

4. **Download** — images stream to `.part` files with exponential backoff,
   adaptive throttling, and size caps.

5. **Verify** — magic-byte check per file. Corrupt or size-limit violations
   are removed.

6. **Archive** — write to `.tmp`, verify (`testzip()` for ZIP, full read for
   TAR), atomic rename. Duplicate pages (SHA-256) are dropped.

7. **Library** — series/chapter rows updated best-effort. The DB never blocks
   downloads.

The scrape-response cache (`cache.py`) serves fresh metadata GETs with zero
network I/O and revalidates stale entries with `If-None-Match`/
`If-Modified-Since`. Only 2xx responses that carried no `Set-Cookie` are
stored; within its TTL a cached good body is served even when the source is
momentarily broken — the intended last-known-good trade-off that `--no-cache`
bypasses.

## Concurrency model

- `--concurrency` bounds parallel image downloads within a chapter (max 32).
- `--chapter-parallel` bounds concurrent chapters of a series (max 8, default 1).
- `--parallel` bounds URLs in flight across a batch (max 16, default 5).
- A shared cooldown window pauses in-flight downloads when a retryable error
  is seen, preventing thundering-herd behavior.

## Security posture

- **SSRF guard**: non-http(s) schemes and hosts resolving to loopback/private/
  link-local/metadata addresses are rejected. Redirect hops are re-validated
  on both the scrape and download paths (capped at `MAX_REDIRECTS` = 5).
- **Untrusted XML**: ComicInfo.xml is parsed with `defusedxml`.
- **SQL injection**: all Library queries use bound parameters.
- **Path traversal**: filenames are sanitized and path-contained before writes.
- **Concurrency bounds**: `--concurrency` values above `MAX_CONCURRENCY` are
  clamped; values below 1 are a hard error.

See [Security Testing](security-testing.md) for the offline test suite.

## Interrupt handling

Ctrl-C (SIGINT) and SIGTERM use a two-stage cooperative model:

1. Sets a `STOP_REQUESTED` flag checked at item boundaries.
2. Second press within 2 seconds force-exits via `os._exit(130)`.

The signal handler only sets the flag or force-exits — it never performs async
teardown itself.

## Platform seam

Most per-OS behavior lives in `platformdirs` (via `config.py`) and the stdlib.
`platform.py` centralizes the handful of conventions that used to be spelled
out at each call site (`os.name`, `platform.machine()`), so packaging targets
and CI smoke checks share one spelling:

- `system()`/`is_windows()`/`is_macos()`/`is_linux()` — canonical OS names.
- `machine()`/`machine_alias()` — normalize `x86_64`/`aarch64` and the vendor
  spellings `amd64`/`arm64` to the same identifier. Artifact names and the
  docs key off these.
- `downloads_dir()` — the real Downloads folder (reads the Windows Shell
  Folders value so OneDrive-redirected homes work), used by the default
  output directory.
- `default_editor()` — `notepad` on Windows, `vi` elsewhere.
- `binary_name()` — adds `.exe` on Windows.

Keep this module deliberately small; platform-sensitive behavior that already
works cross-platform (signal handling, `tempfile.mkdtemp`, `platformdirs`)
stays where it is.

## Versioning and packaging

`pyproject.toml` is the **single hand-edited version source**. At build time it
regenerates `src/comic_dl/_version.py` (`__version__` + `__version_tuple__`)
via:

- the hatchling custom build hook (`packaging/hatch/version_hook.py`,
  wired under `[tool.hatch.build.hooks.custom]`) for every wheel/sdist/editable
  build, and
- `scripts/write-version.py` (also has `--check`) for PyInstaller binaries that
  never build a wheel — so `comic-dl --version` in the standalone exe reports
  the real version instead of falling back to `importlib.metadata` (absent in a
  PyInstaller bundle).

`comic_dl/__init__.py` prefers the static `_version.py`, falls back to
`importlib.metadata`, then `0.0.0.dev0`. The file is committed (a fresh checkout
works before any build) and the drift guard (`tests/test_version.py`,
`scripts/write-version.py --check`) fails the gate if a version bump ships
without its regenerated file.

The release pipeline builds and **installs every artifact in a clean
container/runner and smoke-tests it** (see `docs/develop/releasing.md`):
Debian `.deb`, Fedora `.rpm`, and Arch `.pkg.tar.zst` in both the empty
`stable`/`latest` containers for amd64 and arm64, plus the Windows PyInstaller
exe. macOS binaries and Android packages are explicitly out of scope until
signing/notarization (macOS) and a packaging story exist.

## Extension points

Sources are pluggable. Third-party packages register `Source` classes through
the `comic_dl.sources` entry-point group. Installed plugins appear in
`comic-dl --list-sources` and can override built-ins by setting `priority > 0`.

To contribute a built-in source: create `scrapers/sites/<site>.py` implementing
`BaseScraper` with `scrape()` (and optional `scrape_series()`), decorate with
`@register_scraper(domain=..., capabilities=...)`, and update the supported
sites documentation.
