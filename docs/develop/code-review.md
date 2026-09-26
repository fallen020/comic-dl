# Code Review

How comic-dl's reviews work, from both sides of the reviewer seat. If you are
waiting on a review, the checklist below is what reviewers run — Read it and
pre-fix what it flags.

## Why review here is not generic

Every project has "check the diff", but comic-dl's review culture is shaped by
three constraints:

- **Every outbound fetch passes a SSRF guard** (`validate_request_url` via
  `BaseScraper._timeout_get` / `_open_stream`). A review that waved a new
  `requests.get()` past this could ship a SSRF hole.
- **Rate limiting and retry cooldowns are load-bearing.** New scraping paths must
  ride the existing throttle (`rate.py`) and cooldown, not add their own
  bypass.
- **Tests are offline-only.** A test that performs a live request would corrupt
  deterministic CI and is caught at review, not by the author's machine (which
  has network).

## The checklist

### Structure and scope

- [ ] PR targets `dev`, not `main`/`staging`.
- [ ] One concern per PR; the diff is reviewable in one sitting.
- [ ] `Fixes #NN` present when the PR closes an issue.
- [ ] PR template's Type and Site(s) affected filled in.

### Correctness

- [ ] The code does what the issue/posts say, not just what the diff shows.
- [ ] Error paths handled: partial failures (some chapters fail, others pass)
  are the norm in downloads — check they do not abort the whole run.
- [ ] Interaction with existing abstractions: does it reuse the scraper
  contract, the download pipeline, the archiver? A new parallel implementation of
  an existing thing is a refactor smell.

### Safety (non-negotiable)

- [ ] All new outbound fetches go through the SSRF-guarded helpers; redirects
  are re-validated.
- [ ] No loosening of `rate.py` or the shared retry cooldown.
- [ ] New writes (files, `library.db`) stay inside the output path and use
  sanitized names; archive writes remain atomic (`.tmp` + rename).
- [ ] No new credentials or cookie keys are hardcoded. Cookie values remain
  user-supplied through the cookie jar.
- [ ] Scraper challenge-solving stays opt-in (`solver` defaults to `off`);
  nothing silently enables circumvention.
- [ ] No real-work URLs or series identifiers in tests/examples/docs. Synthetic
  slugs only (`demo-series`, `title_no=1`).

### Tests

- [ ] Bug fixes have a regression test that fails on the old code.
- [ ] New sites/scrapers have a parser test under `tests/scrapers/sites/`
  (offline, mocked HTML).
- [ ] Tests assert on unwrapped content — Rich folds console output at 80 cols
  on CI, so paths split mid-token. Compare against `output.replace("\n", "")`,
  never raw capture. Machine JSON prints with `soft_wrap=True`.
- [ ] No `Path()` constructed while `os.name` is mocked (Python < 3.12
  dispatches to `WindowsPath` and raises on POSIX).
- [ ] New tests actually run in CI (not `skip`ped, not marked network).

### Docs

- [ ] A site/CLI change regenerated docs: `update-sites-docs.py` for site
  tables, `docs.sh` for the mirror and Markdown lint.
- [ ] No aspirational claims: docs describe exact output, not "supports X".

## How to review (the mechanics)

1. **Read the PR title and issue first**, then the diff top-to-bottom. Read
   the diff as a story: routing → scraper → download → archive → library.
2. **Comment inline, specific.** "Line 40: this URL isn't going through
   `_timeout_get`; route it there" beats "please use the safe fetch helper."
   Quote the line.
3. **Use the checklist above mechanically.** Every item is a one-word check;
   a blank field on the checklist is a comment, not an approval.
4. **Run the gates locally** if you have the environment: `./scripts/test.sh`,
   `./scripts/lint.sh`. The repo runs them in CI, but a local run surfaces
   failures faster and with better output than a GitHub "check failed" loop.
5. **Approve with scope stated.** "Approved for the scraper logic; the docs
   regeneration is separate" is honest and useful.

## Reviewing a scraper site PR specifically

New sites are the most common PR. The site-specific audit:

- [ ] Registered in `registry.py` and exported — `--list-sources` shows it.
- [ ] URL routing: which URL shapes does the site accept, and does the scraper
  reject the ones it does not support with a clear error?
- [ ] Pagination and chapter numbering: does it follow the site's actual
  "next" pattern rather than assuming it?
- [ ] Tests cover a parsed (not live) HTML fixture: chapter list, series page,
  pagination, and at least one edge case (missing title, empty list).
- [ ] Site-name spelling consistent with other sites/docs; no brand
  endorsement claims (the README's independent-disclaimer pattern).

## Responding to reviews

- Treat comments as questions first. Ask when a requested fix is unclear.
- Fix what the checklist flags; run the gates again; push new commits.
- Mark threads resolved only when the change actually addresses them.
- Never force-push away the conversation while review is in flight. Squash
  merging is the maintainer's job later.

## When to merge (maintainers)

- Squash-merge only when the checklist is green and CI is green on the PR.
- Keep `git log` on `main` linear: `dev` advances by squashes; `main` only
  receives release commits. A direct push to `main` is a release decision —
  see [releasing.md](releasing.md).
