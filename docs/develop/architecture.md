# Architecture

A guided tour of comic-dl's internals: what each module does, how a URL
becomes an archive, and why the concurrency and security rules exist. Read this
when you first open the source, then use [testing.md](testing.md) and the
[API reference](../api-reference.md) for the contracts.

## The tour: follow one URL end to end

A shell command like `comic-dl -u "<series or chapter URL>"` passes through the
pipeline below. Keep this sequence in your head when reading any source file —
every module plugs into one of these stages.

1. **Entry** — `cli/__init__.py` parses arguments, normalizes and routes the
   URL, and orchestrates the rest.
2. **Routing** — `scrapers/registry.py` resolves the domain to a scraper. If
   no domain scraper matches and the generic fallback is enabled,
   `generic.py` (a yt-dlp-style scraper) picks it up.
3. **Metadata** — the scraper calls `BaseScraper.scrape()` (single chapter →
   `PostMetadata`) or `scrape_series()` (series listing → `SeriesMetadata`).
   Under the hood everything is fetched through `_timeout_get` /
   `_open_stream` in `base.py` — the SSRF guard lives there.
4. **Safety** — every outbound URL passes `validate_request_url`
   (`utils.py`). Redirect hops are re-validated on both the scrape and
   download paths before a socket opens.
5. **Download** — `downloader.py` streams images to `.part` files with
   exponential backoff, per-host throttling (`rate.py`), a shared retry
   cooldown, and size caps (`cli/sizing.py`).
6. **Verify** — each file passes a magic-byte check (`utils.py`); corrupt or
   over-size files are discarded. Duplicate pages (SHA-256) are dropped later.
7. **Archive** — `archiver.py` writes to `.tmp`, verifies (`testzip()` for ZIP,
   full read for TAR), then atomically renames. `comicinfo.py` embeds
   ComicInfo.xml.
8. **Library** — `library.py` records series/chapters in a local SQLite DB,
   best-effort; the DB never blocks downloads.

## Source map

```text
src/comic_dl/
  __main__.py            console entry point
  cli/__init__.py        argument parsing, URL routing, orchestration
  cli/library.py         list/info/latest/remove subcommands
  cli/selection.py       interactive chapter-selection prompt
  cli/sizing.py          size caps, download-size estimates, disk checks
  config.py              config file + platform directory resolution
  platform.py            thin OS seam (system(), machine(), downloads_dir())
  utils.py               URL normalization, sanitization, SSRF guard,
                         image magic-byte verification
  models.py              data contracts (ImageItem, PostMetadata, ChapterInfo,
                         SourceInfo, ScrapedChapter, SeriesMetadata)
  errors.py              error types and exit codes (0/1/2/130)
  downloader.py          streaming/retry download engine + DownloadPipeline
  archiver.py            archive creation (.cbz/.zip/.cbt; atomic writes)
  comicinfo.py           ComicInfo.xml generator
  library.py             local SQLite cache of series/chapters
  ui.py                  rich console output (banner, progress, tables)
  http.py                shared curl_cffi session/request helpers
  cookies.py             persistent cookie jar (SQLite, RFC 6265)
  cache.py               scrape-response cache (TTL, ETag revalidation)
  rate.py                per-site request throttling (token bucket)
  cf.py, antibot.py      Cloudflare detection + solver routing
  webview*.py            system-webview / headless challenge solver
  scrapers/
    registry.py          entry-point discovery, URL → scraper routing
    base.py              shared helpers (meta extraction, validated fetch)
    generic.py           fallback HTML scraper
    madara.py            shared Madara-theme framework scraper
    refresh.py           stale-image refresh registration
    sites/               one module per built-in site
```

The left column is the core pipeline: config → models → download → archive →
library. The right column is the "world interface": everything hostile (HTTP,
cookies, rate limits, Cloudflare). `scrapers/` sits in the middle — it turns
page HTML into the model contracts the pipeline consumes.

## Modules that need care

These modules have invariants beyond their type signatures. Read them fully
before editing.

- `scrapers/base.py` — every fetch path threads through here. Never add a
  fetch helper that bypasses `validate_request_url`.
- `rate.py` and the shared cooldown in `downloader.py` — politeness is
  load-bearing. New code rides them; it never bypasses them.
- `archiver.py` — atomic writes (`.tmp` + rename). A change that writes
  in-place risks corrupting an archive on interrupt.
- `errors.py` + `ui.py` — user-facing text flows only through the `ui.py`
  helpers. Error *types* in `errors.py` map to exit codes; message text never
  leaks raw exception args.
- `webview*.py` — subprocess solver. The parent/child protocol is defined in
  `webview_constants.py`; the child runs headless and communicates over JSON.

## Concurrency model

Three independent bounds, each clamped in `cli/__init__.py`:

- `--concurrency` — parallel image downloads within a chapter (max 32).
- `--chapter-parallel` — concurrent chapters of a series (max 8, default 1).
- `--parallel` — URLs in flight across a batch (max 16, default 5).

Above the caps the value is clamped, not rejected; below 1 is a hard error
(better to fail loudly than to deadlock). A shared cooldown pauses in-flight
downloads when a retryable error is seen, so N retries do not turn into a
thundering herd.

## Security posture

The threat model is "the user hands us URLs from untrusted sites." Everything
below is enforced in code, tested in `tests/security/`, and re-checked at
review:

- **SSRF guard** — non-http(s) schemes and hosts resolving to
  loopback/private/link-local/metadata addresses are rejected. Redirect hops
  are re-validated on both scrape and download paths (capped at
  `MAX_REDIRECTS` = 5). See [security-testing.md](security-testing.md) for the
  known DNS-rebinding (TOCTOU) limitation and what would change it.
- **Untrusted XML** — ComicInfo.xml parses with `defusedxml` (XXE and
  billion-laughs rejected).
- **SQL injection** — all Library queries use bound parameters.
- **Path traversal** — filenames sanitized and path-contained before writes.
- **Concurrency bounds** — values above the cap clamp; values below 1 error.

When you touch any of these, the corresponding test in
[security-testing.md](security-testing.md) must be updated in the same PR.

## Interrupt handling

SIGINT/SIGTERM use cooperative two-stage shutdown:

1. A `STOP_REQUESTED` flag is set; it is checked at item boundaries.
2. A second press within 2 seconds force-exits via `os._exit(130)`.

The signal handler only sets the flag or force-exits — it never performs async
teardown. Do not add cleanup logic into the handler; put it where the flag is
checked.

## Platform seam

Per-OS behavior lives mostly in `platformdirs` (via `config.py`) and stdlib.
`platform.py` centralizes the handful of conventions that used to be spelled
out at each call site, so packaging and CI smoke checks share one spelling:

- `system()` / `is_windows()` / `is_macos()` / `is_linux()` — canonical names.
- `machine()` / `machine_alias()` — normalize `x86_64`/`aarch64` and the
  vendor spellings `amd64`/`arm64`. Artifact names and docs key off these.
- `downloads_dir()` — the real Downloads folder (reads the Windows Shell
  Folders value so OneDrive-redirected homes work).
- `default_editor()` — `notepad` on Windows, `vi` elsewhere.
- `binary_name()` — adds `.exe` on Windows.

Keep this module small. Behavior that already works cross-platform (signals,
`tempfile.mkdtemp`, `platformdirs`) stays where it is.

## Versioning and packaging

`pyproject.toml` is the single hand-edited version source. At build time it
regenerates `src/comic_dl/_version.py` (via `packaging/hatch/version_hook.py`
for wheel/sdist builds, `scripts/write-version.py` for PyInstaller binaries).
`_version.py` is committed so fresh checkouts work before any build; the drift
guard (`tests/test_version.py`, `write-version.py --check`) fails the gate if
a version bump ships without its regenerated file.

## Extension points

Sources are pluggable through the `comic_dl.sources` entry-point group.
Installed plugins appear in `comic-dl --list-sources` and can override
built-ins with `priority > 0`.

To add a built-in source: create `scrapers/sites/<site>.py` implementing
`BaseScraper` with `scrape()` (and optional `scrape_series()`), decorate with
`@register_scraper(domain=..., capabilities=...)`, then run
`uv run python scripts/update-sites-docs.py` to refresh the supported-sites
tables. See [writing a plugin](../usage/write-plugin.md) and the runnable
plugin example in `examples/plugin-example/`.
