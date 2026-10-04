# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org).

## [v0.0.5] - 2026-10-04

### Added

- Stable `site_error_code` values now surface a fix hint: a central
  per-code hint map fills in whenever a failure carries a code but no
  hand-written hint, so blocked, locked, or moved chapters always print
  the cause plus what to do.
- Interrupt resume hint echoes the real command run (canonical program
  name, prompt-entered URL included).
- Downloaded images are structurally verified; truncated and polyglot
  pages are rejected instead of archived.
- Help screen rebuilt from the parser; `self site` live-checks test URLs
  for 27 adapters with kind-aware checks.
- Single shared retry path (`retry_transient`) for scraper-side fetches.
- Hivetoons series roster parses the site's TanStack stream payload.
- Website: `llms.txt` generation, Explain-with-AI dropdown, namespaced
  tab sets, responsive shell, one-row-per-site tables with live filter.

### Changed

- Site adapter bumps: asurascans 1.0.3, tapas 1.0.2, flamecomics 1.0.3,
  ehentai 1.0.3, kagane 1.0.3, hivetoons 1.0.3.
- Dependencies: attest-build-provenance v4.2.2, hatchling >= 1.32.4,
  mypy 2.4.0, ruff 0.16.10, platformdirs 4.12.3, astro 7.3.5,
  @lucide/astro ^1.49.0.
- Arch package renamed to avoid an AUR collision; CI artifact retention
  capped; config starter slimmed.

### Fixed

- WAF/challenge misclassification: Asura, Tapas, and FlameComics
  Cloudflare challenges now report `SITE_BLOCKED` with a solver hint
  instead of a network error; Kagane API 404s report removal instead of
  request failure; Stonescape maps non-404/403 HTTP errors; E-Hentai
  raises on retry exhaustion and API errors.
- `site_error_code` coverage across the remaining adapters (malformed
  URLs, locked chapters, ValiScans platform).
- Interrupt hint no longer leaks the launcher path; library errors no
  longer leak SQLite driver text.
- Gedecomix test URL repointed to a live chapter; FSIComics series
  scraping restored on the Foxiz theme; `self site` columns corrected.
- SSRF, content-length, referer, and cookie-permission hardenings; rate
  limiter keyed by hostname; host park lifted on success.

## [v0.0.4] - 2026-09-27

### Added

- Ten new built-in sources — DivaScans, HD Porn Comics, IMHentai,
  KodokuEasyAccess, ManhuaTo, Nyx Scans, Tapas, ToonVerse, ValirScans, and
  Vortex Scans — taking built-in support from 21 to 29 sites.
- First-run legal notice.
- Stable `site_error_code` failure classification across scrapers, surfaced
  in errors and batch output.
- Per-page download state manifest with a resume-safe retry pass; partial
  chapters tracked separately in batch footers.
- Connectivity probe before blaming the network for failures.
- Library schema v4: one global database with per-row output roots, global
  library commands, and `library update --dry-run`.
- Leading `m.` mobile subdomains normalized away before matching.

### Changed

- KodokuStudio replaced by KodokuEasyAccess (new domain).
- System-webview solver off by default.
- Archive writes stream to disk instead of buffering whole image sets.
- Website design refreshed; dev guides rewritten as end-to-end processes.

### Removed

- ManhwaZ support dropped.

### Fixed

- Kagane reports the Cloudflare challenge instead of a network error.
- Batch output honesty: `Saved:` gated on success, per-page failure reasons,
  chapter/partial accounting; single-URL polish (short chapter titles, one
  completion line).
- Tapas cover/creator/description; Asura Scans full series summary; GenzToons
  listing guard, locked chapters, metadata cleanup; Vortex/Nyx cover,
  synopsis, genres.
- Cookie collision resolution, charset-aware cache decode, cookie-replay
  probing with a JSON cache-poisoning guard; `.partial` markers restored to
  on-disk page names so reruns skip them.

### Security

- Cookie jar encrypted at rest.
- Outbound fetches fail closed on DNS/validation errors; rebinding window
  narrowed.
- 2xx media payloads never treated as antibot challenges; DataDome markers
  tightened.

## [v0.0.3] - 2026-09-20

### Added

- `comic-dl self version` and `comic-dl self update` — detects whether comic-dl
  was installed by apt, dnf, pacman, pip, uv, a source checkout, or a standalone
  binary and updates it through the owning package manager (with `--yes`,
  sudo-when-needed). Never touches a source checkout or unwritable environment;
  it reports and instructs instead.
- `comic-dl self site list` / `check` / `update` — per-site support versioning:
  every built-in adapter ships with a semantic version and minimum core version,
  checked against the `site-support.json` manifest attached to each release.
- Eight new built-in sources: HiveToons, GenzToons, Qimanga, StoneScapes,
  EN-ThunderScans, KingOfShojo, ManhwaTop, and LGBTics (taking built-in support
  from 13 to 21 sites).
- Built-in scrapers are auto-discovered from the registry; a broken adapter
  module fails fast instead of silently disappearing from `--list-sources`.

### Changed

- Adapter versioning rules and update flow documented in
  `docs/usage/site-support.md`; self-update strategies in
  `docs/usage/self-update.md`.
- Docs and website refreshed for v0.0.3 (SEO/AI discoverability, accessibility,
  brand icons, regenerated supported-sites tables).

### Fixed

- `self update` asset selection and `--yes` paths race-free and arch-correct on
  Apple Silicon CI runners.
- Update checks no longer mis-report a dev snapshot as "up to date" against the
  published release with the same number.

## [v0.0.2] - 2026-09-15

### Added

- WeebCentral as a built-in source.
- Bare positional URL shorthand: `comic-dl <url>` instead of `comic-dl -u <url>`.
- `--chapters` flag now accepts canonical chapter numbers (including `0`).
- `comic-dl plugin list`, `validate`, and `scaffold` commands for plugin
  management.
- Typo-aware "Did you mean" suggestions when a flag is misspelled.
- Plugin and Madara series URLs routed via `matches_series_url`.

### Fixed

- Cloudflare replay sessions re-use cookies on blocked downloads.
- FlameComics: correct series metadata derivation, deduplication, and chapter
  sort order.
- E-Hentai: URL parsing hardened, throttle recovery improved, size estimates
  use display images.
- Dry-run mode: series previews point at the real series folder path.
- Asura Scans: friendly 404s and CDN host hardening.
- WebTOON: episode number read from `data` attribute; duplicate helpers removed.
- Diagnostic output sanitized at the error boundary; unified error taxonomy
  across scrapers.
- PyInstaller binary now bundles `banner.txt`.
- `asyncio.as_completed` replaced in dry-run overlay to fix hanging tasks.
- Parse errors, summary, and progress styling polished.

### Security

- Probe redirects validated before dispatch.
- Cookie jar rejects public-suffix cookies; Secure flag enforced on replay.
- Identity hashes use SHA-256 (replaced MD5).
- Solver URL validated before use.
- Download-path deduplication prevents staging-directory collisions across
  chapters.

## [v0.0.1] - 2026-09-06

First public release. comic-dl downloads comic and manga galleries from
supported websites and compiles them into verified CBZ, ZIP, and CBT archives.

### Added

- Chapter and series downloads from 12 built-in sources (Pawchive, E-Hentai,
  WEBTOON, FlameComics, FSIComics, GEDE Comix, Asura Scans, Kagane, Toonily,
  MangaDex, Manhwaz, KodokuStudio) plus a generic fallback scraper and a
  pluggable source interface so new sites can be added without forking.
- CBZ, ZIP, and CBT archive output with `ComicInfo.xml` metadata.
- SQLite-backed library management (`list`, `update`, `remove`, `restore`)
  with per-host rate limiting, retry with backoff, response caching, and
  cookie-jar support.
- Cloudflare challenge handling via HTTP/TLS impersonation with an optional
  system-webview solver fallback.
- Resumable interrupted downloads via Range requests and `.partial` markers;
  magic-byte image verification and atomic archive writes.
- TOML configuration, shell completions (bash/zsh/fish), JSON output mode,
  and plain-Markdown documentation under `docs/`.
- Prebuilt packages for Debian/Ubuntu, Fedora/RHEL, and Arch Linux (amd64)
  plus a standalone Windows executable.

### Security

- All outbound requests pass SSRF validation before dispatch.
- Dependency sets are pinned and locked; CI runs CodeQL, pip-audit, and an
  offline-safe test suite.
