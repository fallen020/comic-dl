## Summary

**What does this PR change?**

**Why is it needed?**

- **Related issue:** _(e.g. `Closes #123`, or "none")_

- **Type:** _(check all that apply)_

  - [ ] New site adapter (`scrapers/sites/`)
  - [ ] Site fix (existing adapter)
  - [ ] Bug fix (core)
  - [ ] Documentation
  - [ ] Refactor / cleanup
  - [ ] Dependency
  - [ ] Packaging / CI
  - [ ] Breaking change (CLI, config, or public API)

- **Site(s) affected:** _(e.g. flamecomics, ehentai — or "none")_

## Test results

What you ran and what happened (pass/fail; explain failures and skips):

**Not run and why:**

## Checklist

Core (every PR):

- [ ] `./scripts/test.sh`
- [ ] `./scripts/lint.sh`
- [ ] No unrelated changes

Docs (pick one):

- [ ] No documentation changes needed
- [ ] `docs/` updated and `./scripts/docs.sh` passes
- [ ] `README.md` updated (landing page only; details live in `docs/`)

Site adapters (new or fix):

- [ ] Parser tests added/updated in `tests/scrapers/sites/` (offline fixtures via `tests/helpers.py`, not live HTTP)
- [ ] Adapter `version` bumped for parsing changes; `./scripts/docs.sh` passes (regenerates manifest + supported-sites table)
- [ ] New adapter is picked up by auto-discovery (`@register_scraper` on a non-`_`-prefixed module — enforced by `test_every_site_module_registers`; no registry edit needed)
- [ ] URL patterns and chapter/pagination covered by tests; shared fetch/retry/rate-limit helpers used, no custom HTTP loops
- [ ] Live smoke test (supplementary evidence): sanitized URL tested, auth needed?, behavior verified. Redact cookies/tokens/headers/paths from pasted output, and don't hammer the site.

Dependencies (only if checked above):

- [ ] Why it is needed is stated in Summary; `uv.lock` refreshed (`uv lock`); license noted

Breaking change details (only if checked above):

**Breaking-change details:**

## Reviewer notes

<!-- Known limitations, edge cases, or what deserves extra attention. -->
