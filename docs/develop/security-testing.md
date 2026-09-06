# Security Testing

The security test suite exercises network safety, filesystem hardening, and
parser resilience. Every test is **offline** — no live network calls.

## Running the suite

```bash
# Full security suite
uv run pytest tests/security/ -q

# Verbose, no cache
uv run pytest tests/security/ -q -p no:cacheprovider

# Full test suite (includes security)
uv run pytest tests/ -q
```

## Network safety

`tests/security/test_network_safety.py` covers:

- **Hostile URL rejection** — URLs resolving to loopback, private, link-local,
  or metadata addresses (e.g. `127.0.0.1`, `10.0.0.0/8`, `169.254.169.254`,
  `::1`, IPv4-mapped `::ffff:127.0.0.1`) are blocked before any socket opens.
- **Pre-connect validation** — the guard is enforced before a connection is
  established, not after.
- **Redirect re-validation** — every hop in a redirect chain is re-checked; a
  loopback hop introduced mid-chain is blocked.
- **Redirect budget** — chains capped at `MAX_REDIRECTS` (5).
- **Scrape-path validation** — `BaseScraper._timeout_get` rejects private URLs
  before any request. Redirect chains hopping through `127.0.0.1` are aborted.
- **Scraper entry validation** — e-hentai and pawchive validate their initial
  URLs; relative links are resolved to absolute before fetching.

### Known limitation: DNS rebinding (TOCTOU)

The SSRF guard resolves each hostname at *validation* time, but curl_cffi
resolves again at *connect* time. A server answering with a public address
during validation and a private address moments later could bridge that
check-then-use gap. This is inherent to validating ahead of the HTTP
library's own resolution and is accepted here because targets are
user-chosen scrape sites and every redirect hop is re-validated. Changing
this would require pinning resolved IPs into the connection itself.

## Filesystem, database, and parser hardening

`tests/security/test_filesystem_db_parser.py` covers:

- **Path containment** — `sanitize_filename` never yields an absolute path or
  `..` traversal. A hostile `ImageItem.filename` cannot escape the output dir.
- **SQL parameterisation** — all Library queries use bound parameters. Quote-laden
  or wildcard-laden titles are treated as data, not SQL.
- **Schema migration safety** — a database with an unknown future `user_version`
  is refused rather than corrupted.
- **Untrusted XML** — ComicInfo.xml is parsed with `defusedxml`: XXE and
  billion-laughs payloads are rejected.
- **Concurrency bounds** — `--concurrency` values above `MAX_CONCURRENCY` are
  clamped; values below 1 are a hard error.

## Test infrastructure

`tests/security/_server.py` provides:

- `FakeHttpServer` — async loopback server with routes for plain responses,
  redirect chains (including hostile hops), and mid-stream failures.
- `NetHttpClient` / `NetResponse` — minimal `curl_cffi`-shaped client that
  mimics `AsyncSession.stream()` so the downloader's guarded code paths are
  exercised end to end.

## Related hardening

- SSRF guard: `comic_dl/utils.py` (`validate_request_url`, `resolve_redirect_url`)
- Downloader integration: `comic_dl/downloader.py` (`_open_stream`, `_try_resume`)
- Scrape-path: `comic_dl/scrapers/base.py` (`_timeout_get`, capped redirects)
- Concurrency clamp: `comic_dl/cli/__init__.py`
- Untrusted XML: `defusedxml` (see `pyproject.toml`)
