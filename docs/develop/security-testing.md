# Security Testing

Where comic-dl's hardening lives and how it is tested. The security suite
(`tests/security/`) is the product's security spec: every behavior below is
encoded as a failing-if-regressed test. If you touch one of these modules,
update the matching test in the same commit.

## Threat model

comic-dl scrapes arbitrary third-party sites. The user hands the program URLs;
untrusted responses (pages, images, XML, redirect chains) flow from there into
the application. The suite assumes every input is hostile and proves the code
stays inside three boundaries: **network**, **filesystem/database**, and
**parser** resilience.

Every test in this suite is offline — it exercises the guards against a
loopback fake host, never the live internet. That keeps the security net
deterministic in CI.

## Running the suite

```bash
# Just the security net
uv run pytest tests/security/ -q

# The whole suite (includes security)
./scripts/test.sh

# Security suite + coverage
uv run pytest tests/security/ --cov=comic_dl --cov-report=term-missing
```

## Network safety

`tests/security/test_network_safety.py` is the SSRF net. What it pins down:

- **Hostile URL rejection** — URLs resolving to loopback, private, link-local,
  or metadata addresses (`127.0.0.1`, `10.0.0.0/8`, `169.254.169.254`, `::1`,
  IPv4-mapped `::ffff:127.0.0.1`, ...) are blocked **before any socket opens**.
- **Pre-connect validation** — the guard runs before a connection is
  established, not as part of retry handling.
- **Redirect re-validation** — every hop in a chain is re-checked; a loopback
  hop introduced mid-chain aborts the fetch.
- **Redirect budget** — chains are capped at `MAX_REDIRECTS` (5).
- **Scrape-path validation** — `BaseScraper._timeout_get` rejects private URLs
  before requesting; redirect chains that hop through `127.0.0.1` abort.
- **Scraper entry validation** — site scrapers validate their initial URLs and
  resolve relative links to absolute before fetch.

### Known limitation: DNS rebinding (TOCTOU)

The guard resolves each hostname at *validation* time; the HTTP library
(`curl_cffi`) resolves again at *connect* time. A server that answers with a
public address during validation and a private address moments later can
bridge the check-then-use gap. This is inherent to validating ahead of the
transport's own resolution, and it is accepted because targets are
user-chosen scrape sites and every redirect hop is re-validated. Closing it
would mean pinning resolved IPs into the connection itself.

## Filesystem, database, and parser hardening

`tests/security/test_filesystem_db_parser.py` covers:

- **Path containment** — `sanitize_filename` never yields an absolute path or
  `..` traversal; a hostile `ImageItem.filename` cannot escape the output dir.
- **SQL parameterization** — every Library query uses bound parameters;
  quote-laden or wildcard-laden titles are data, never SQL.
- **Schema migration safety** — a DB with an unknown future `user_version` is
  refused, not migrated or corrupted.
- **Untrusted XML** — ComicInfo.xml parses with `defusedxml`: XXE and
  billion-laughs payloads are rejected.
- **Concurrency bounds** — `--concurrency` above `MAX_CONCURRENCY` clamps;
  below 1 is a hard error (fail loudly rather than deadlock).

## How the tests fake the world

- `tests/security/_server.py`:
  - `FakeHttpServer` — async loopback server with routes for plain responses,
    hostile redirect chains, and mid-stream failures.
  - `NetHttpClient` / `NetResponse` — a minimal `curl_cffi`-shaped client that
    mimics `AsyncSession.stream()`, so the **downloader's real guarded code
    paths** run end to end without the internet.
- Site scraper tests reuse a shared `<site>.html`-fixture helper to exercise
  the routing + validation that would otherwise depend on live DNS.

## Where the guards live (in case of a hit)

| Guard | Implementation |
| :---- | :------------- |
| SSRF validation + redirect resolution | `comic_dl/utils.py` (`validate_request_url`) |
| Downloader streaming + resume | `comic_dl/downloader.py` (`_open_stream`, `_try_resume`) |
| Scrape-path validation + cap | `comic_dl/scrapers/base.py` (`_timeout_get`, capped redirects) |
| Concurrency clamp | `comic_dl/cli/__init__.py` |
| Untrusted XML | `defusedxml` (see `pyproject.toml`) |

## What is deliberately *not* covered

- **Rate limiting hardening** — availability/abuse of third-party sites from
  polite batch downloading is out of scope (see `SECURITY.md`). The
  politeness layer (`rate.py`, shared cooldown) is tested for behavior, not
  adversarial throughput.
- **Live-site integration** — no test depends on an external site being up.
  Adding one is a review failure, not an improvement.

## Review checklist (recap, in one line)

Every outbound fetch routes through a `validate_request_url`-guarded helper.
Fixes that touch the guards above must carry their regression test, or the
review checklist in [code-review.md](code-review.md) will bounce them.
