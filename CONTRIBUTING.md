# Contributing

Thanks for contributing to `comic-dl`! This project is small and
community-driven; every issue, PR, and review helps.

Questions? Open a [Discussion](https://github.com/fallen020/comic-dl/discussions)
or see [.github/SUPPORT.md](.github/SUPPORT.md) for the full help routing.

## Repository health

This repository uses GitHub's built-in project management to organise work:

- **Issues** — feature requests and bug reports. Use the provided templates.
- **Labels** — a fixed vocabulary is used to triage issues:
  - `bug`, `enhancement`, `documentation`
  - `good first issue`, `help wanted`
  - `needs-triage`, `needs-repro`, `priority`
  - Site labels — one per supported source, matching the order in
    [Supported Sites](docs/reference/supported-sites.md):
    `site:pawchive`, `site:e-hentai`, `site:webtoon`, `site:flamecomics`,
    `site:fsicomics`, `site:gedecomix`, `site:asurascans`, `site:kagane`,
    `site:mangadex`, `site:toonily`, `site:manhwaz`, `site:kodokustudio` — mark which source a
    report/PR touches
- **Projects** — a kanban board (`Backlog → In progress → In review → Done`)
  mirrors the issue queue. Maintainers move cards as work proceeds.

## Development setup

Requires **uv** ([install guide](https://docs.astral.sh/uv/getting-started/installation/)).

```bash
git clone https://github.com/fallen020/comic-dl
cd comic-dl
uv sync --extra dev --locked
```

The wheel and binaries are covered in `README.md`.

### Scripts
- **Run tests:** `./scripts/test.sh`
- **Test with coverage:** `./scripts/test.sh --cov=comic_dl`
- **Lint + type check + security scan:** `./scripts/lint.sh`
- **Build a package (sdist + wheel):** `./scripts/build.sh`
- **Build a one-file binary:** `./scripts/build-binary.sh` (Windows: `.ps1`)

You can also run these directly with `uv`. All tests use local mocks and
fixtures; no external services are contacted.

## Documentation ownership

`docs/` is the single source of truth for documentation. **Any documentation
beyond installation, a minimal example, and project overview belongs in
`docs/`.** If a change requires updating both `README.md` and a page under
`docs/`, reconsider whether the README is becoming too detailed.

- `README.md` is the GitHub landing page: what the project is, why, how to get
  it running in 60 seconds, and links into `docs/`.
- `docs/` is the source of truth. It stays plain Markdown so pages read fine on
  GitHub. Validate with `uv run --extra docs pymarkdown scan docs README.md`.
- The [API reference](docs/api-reference.md) is maintained by hand — keep
  docstrings current when you change a public contract.

## Docstrings & comments

Docstrings follow the Google style, summarized in one line, and are enforced
for presence by ruff (rules `D100`–`D104` and `D106` via `scripts/lint.sh`).

- **One-line summaries**: `"""Make ``name`` safe for use as a file name."""`
  Use `"""` with a blank line inside for multi-line docstrings
  (Args/Returns/Raises sections come next, wrapped at 72 columns).
- **Every module** gets a one-line module docstring; **every public class and
  top-level public function** gets one too. Write these to a new person —
  what does this code do, and why does it exist?
- **Comments**: explain *why*, not *what*. The code already says what it
  does; `# Update the throttle window` repeats the next line, while
  `# 509 is e-hentai's H@H bandwidth throttle — back off instead of failing`
  records knowledge the code can't express.
- **Don't polish**: this project intentionally ignores pydocstyle's style
  rules (`D200`–`D419`) and method-level presence rules (`D102`/`D105`/`D107`);
  don't add docstrings to trivial accessors or magic methods for its sake.

## Making changes

1. Ping the issue you're addressing, or open one first for larger changes.
2. Work on a feature branch (`git checkout -b fix/descriptive-name`).
3. Keep changes focused. When you add a new supported site, also update
   `docs/reference/supported-sites.md` and add a scraper under `src/comic_dl/`.
4. Add or update tests. Bug fixes need a regression test; new features need
   coverage of the new code path.
5. Run the checks in the Scripts section (tests + lint + type check) before
   pushing.
6. Open a pull request using the template.

## Branching & CI

- Development happens on **feature branches** merged to `main` via reviewed
  pull requests.
- CI (`.github/workflows/ci.yml`) runs tests, ruff, mypy, bandit, and a
  dependency audit on every push/PR across Linux, macOS, and Windows.
- Packaging (`.github/workflows/packaging.yml`) validates the distro packages
  and Windows exe on PRs; tags trigger the full release
  (`.github/workflows/release.yml`).
- Once the repository is hosted, maintainers should enable **branch
  protection** on `main` requiring a green CI run and a review before merge.
- Releases follow the runbook in [`docs/develop/releasing.md`](docs/develop/releasing.md).

## Pull request checklist

See [the PR template](.github/pull_request_template.md). Every PR should:

- Reference the issue it closes on: `Fixes #123`.
- Describe the change and the reasoning behind it.
- List the tests you ran (and ideally add a new one).
- Note which theme / site the change touches so reviewers can triage fast.

## Reviewing

Reviewers should look for: behavioural correctness, test coverage of new paths,
adherence to the codebase's existing patterns (scrapers follow the contract in
`scrapers/base.py`), and documentation that was updated in step. When in doubt, ask
constructively rather than guessing.

## Code of conduct

Please follow our [Code of Conduct](CODE_OF_CONDUCT.md). Be respectful and
constructive.