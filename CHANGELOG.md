# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org).

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
