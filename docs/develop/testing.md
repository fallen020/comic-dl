# Testing

comic-dl's test suite is designed to be **offline-only** and deterministic.

## Running tests

```bash
# Full suite
uv run pytest tests/ -q

# Single file
uv run pytest tests/test_downloader.py -q

# With coverage
uv run pytest tests/ --cov=comic_dl

# Using the helper script
./scripts/test.sh
```

## Test structure

| File | Focus |
| :--- | :---- |
| `test_cli.py` | CLI parsing, flags, subcommands |
| `test_downloader.py` | Download pipeline, retries, resume, verification |
| `test_archiver.py` | Archive creation, dedup, verification, atomic write |
| `test_library.py` | SQLite library operations |
| `test_cache.py` | Cache freshness, conditional requests, TTL |
| `test_http.py` | Cookie jar, referer headers, client args |
| `test_config.py` | Config loading, validation, precedence |
| `test_scraper.py` | Base/`BaseScraper` behavior |
| `test_scrapers/sites/` | Per-site scraper parsing logic (one test file per site) |
| `test_models.py` | Dataclass contracts |
| `test_utils.py` | URL validation, sanitization, magic bytes |
| `test_webview.py` | Webview solver availability, subprocess |
| `test_generic_routing.py` | Generic fallback scraper routing |
| `test_perf_regressions.py` | Performance smoke tests (marked `perf`) |

## Security tests

The security test suite in `tests/security/` is deliberately offline. Every
test spins up its own loopback HTTP server.

```bash
uv run pytest tests/security/ -q
uv run pytest tests/security/ -q -p no:cacheprovider
```

See [Security Testing](security-testing.md) for coverage details.

## Coverage

Coverage threshold: 80% (excludes `webview.py` and `test_webview.py` which
require GUI interaction).

```bash
uv run pytest tests/ --cov=comic_dl --cov-report=term-missing
```

## Writing tests

- Bug fixes need a regression test.
- New features need coverage of the new code path.
- All tests must work without network access.
- Use the existing fixture patterns in `tests/` for mocked HTTP responses.
