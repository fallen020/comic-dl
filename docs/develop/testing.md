# Testing

comic-dl's test suite is **offline-only and deterministic**: no live network
calls anywhere, so the suite never flakes and CI is reproducible. This page is
how to run it, how to find the right test, and how to write a new one without
breaking those two properties.

## Running the suite

```bash
# Full suite (parallel, no cache) — this is what CI runs
./scripts/test.sh

# Focused: one file or one directory
uv run pytest tests/test_downloader.py -q
uv run pytest tests/scrapers/sites/ -q

# With coverage
uv run pytest tests/ --cov=comic_dl

# Security suite alone
uv run pytest tests/security/ -q
```

`-k` selects by name:

```bash
uv run pytest tests/ -q -k "retry and not resume"
```

Tests marked `perf` (performance smoke tests) run with the rest; drop them fast
with `-m "not perf"` if a run feels slow.

## Where a test for X lives

| You changed... | Test file |
| :--- | :--- |
| CLI parsing, flags, subcommands | `test_cli.py` |
| A downloader behavior (retries, resume, verification) | `test_downloader.py` |
| Archive packing | `test_archiver.py` |
| Local SQLite library | `test_library.py`, `test_library_cli.py` |
| Scrape cache | `test_cache.py`, `test_cache_cli.py` |
| HTTP client, cookies | `test_http.py`, `test_cookiecrypt.py` |
| Config loading, precedence | `test_config.py` |
| Scraper framework (`base.py`, madara, generic) | `test_scraper*.py`, `test_madara.py`, `test_generic*` |
| A specific site | `tests/scrapers/sites/test_<site>.py` |
| Models, utils, errors | `test_models.py`, `test_utils.py`, `test_errors.py` |
| Webview solver | `test_webview.py`, `test_webview_solver.py` |
| Version handling | `test_version.py` |
| Plugin loading | `test_plugin_cli.py`, `test_registry.py`, `test_sources.py` |
| Network/filesystem/db safety | `tests/security/` (see [security-testing.md](security-testing.md)) |
| Cross-platform behavior | `test_platform.py` |

If your change touches two of those rows, both files need test updates.

## The offline-only rule

Every test must work with no network. That is enforced by convention, not a
flag, so a live request sneaks through if nobody catches it. The patterns that
keep the suite offline:

- **Scraper tests** feed the parser a captured HTML fixture (a string in the
  test) and assert on the parsed result. There is a test
  `scrapers/sites/` per site; add a page-shaped fixture for each page type the
  site serves.
- **HTTP/download tests** use the loopback server in `tests/security/_server.py`
  (`FakeHttpServer`) or mock the transport.
- **Never** make a real web request from a test, even a tiny "is this URL up"
  check. Contribute a fixture instead.

## Assertion conventions

- **Assert on unwrapped content.** Rich folds console output at 80 columns on
  CI, so a path or phrase can split mid-token in the captured output. Compare
  against `output.replace("\n", "")`, never the raw capture string. Machine
  JSON must print with `soft_wrap=True` so a narrow terminal cannot corrupt
  it.
- **Never construct `Path()` while `os.name` is mocked.** On Python < 3.12
  the mocked value dispatches to `WindowsPath` and raises on POSIX — and it
  crashes pytest's own failure renderer the same way. Mock the seam
  (`platform.machine()` etc.), not `os`.
- Prefer asserting a behavior (an archive exists, a row was inserted, a page
  was dropped as duplicate) over asserting implementation details.

## Writing a regression test

Bug fixes get a test that fails on the old code. The shape:

```python
def test_duplicate_pages_are_dropped(tmp_path):
    archive = build_archive(tmp_path, ["a.png", "a.png"])
    pages = list(archive)
    assert pages == ["a.png"]  # second copy removed
```

Rules that make regression tests hold up:

- **Offline.** A regression test that needs the network will be reverted in
  CI. Build the condition with fixtures.
- **Focused.** It tests the exact failure, not a happy-path cousin.
- **Named for the behavior**, not the symptom: `test_duplicate_pages_are_dropped`
  not `test_archive_fix`.

## Coverage

The floor is **80%** (excluding `webview.py` and `webview_solver.py`, which
need a GUI). CI enforces it by running test.sh with `--cov`, and the report
fails the run at `fail_under = 80`; execute coverage locally with
`--cov-report=term-missing` to see the gaps. Coverage is a floor, not a
target — a scraper's weird branch is worth a test even when the number is
already green.

## When tests are the product

Two suites act as specs rather than regression nets:

- `tests/security/` — encodes the SSRF guard, path containment, SQL
  parameterization, defused XML, and concurrency clamps. A change that touches
  any of those must update the corresponding security test in the **same
  commit**.
- `tests/test_version.py` — pins version drift: a version bump without the
  regenerated `_version.py` fails here.

See [security-testing.md](security-testing.md) for the security suite's inner
workings.
