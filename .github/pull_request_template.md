## What & why

<!-- What does this PR do, and why? Attach an issue number if there is one. -->

- **Type:** _(check all that apply)_

  - [ ] New scraper / plugin
  - [ ] Site fix
  - [ ] Bug fix
  - [ ] Documentation
  - [ ] Refactor / cleanup
  - [ ] Dependency
  - [ ] Breaking change (CLI, config, or public API)

- **Site(s) affected:** _(e.g. pawchive, e-hentai, mangadex — or "none")_

## Checklist

Docs:

- [ ] No documentation changes needed
- [ ] `README.md` updated
- [ ] `docs/` updated (incl. `docs/reference/supported-sites.md` for support changes)

Tests / checks _(run what applies; site-only or doc changes may not need all)_:

- [ ] `./scripts/test.sh`
- [ ] `./scripts/lint.sh`
- [ ] `uv run --extra docs pymarkdown scan docs README.md` (when `docs/` or `README.md` changed)
- [ ] Manual smoke test against a live site (scraper changes)

For scraper PRs, confirm:

- [ ] Scraper is registered / exported in `registry.py`
- [ ] URL patterns and chapter/pagination handling work (tested)
- [ ] Site-specific parser tests included/updated in `tests/scrapers/sites/`
- [ ] Site added to `--list-sources` output expectations, where asserted

## Reviewer notes

<!-- Known limitations, edge cases, or what deserves extra attention. -->

## Related issue

Closes #