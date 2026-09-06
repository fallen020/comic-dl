# Releasing

How a new version of comic-dl ships. This runbook makes releases repeatable.

> [!NOTE]
> The first public release (`0.0.1`) ships the same way as every later one:
> push `main`, tag `v0.0.1`, and let `release.yml` build, validate, and attach
> the artifacts. The one-time repository-side steps (repo creation, verified
> identity) are in
> [First release — repository setup](#first-release--repository-setup) below.
>
> If this is the first release of a brand-new repository, start with that
> section first; everything after it assumes the repository, remote, and GPG
> key are already in place.

## First release — repository setup {#first-release--repository-setup}

The `0.0.1` release is the first push of a local, single-commit history. These
GitHub-side steps happen once, before the first tag:

1. **Create a repository** — `fallen020/comic-dl`, public, initialized empty
   (no README, license, or `.gitignore`), so the first push has no unrelated
   history to reconcile.
2. **Verified identity** — add the commit author email and the GPG public key
   (`gpg --armor --export D784B9E3D5FA85D2`) to the GitHub account so commits
   and tags display as Verified.
3. **Push** — `git remote add origin <url> && git push -u origin main`.

PyPI publishing is deferred: the `comic-dl` name on PyPI is held by an
unrelated project, and `release.yml` attaches the sdist + wheel to the GitHub
Release instead. `pip install comic-dl` is not available until that changes.

Optional publisher channels (AUR/COPR/winget) require their own secrets and
stay commented out in `release.yml` until configured.

## Versioning

comic-dl follows [Semantic Versioning](https://semver.org). The single source
of truth for the version is `version` in `pyproject.toml`. All builds
regenerate `src/comic_dl/_version.py` from it (hatchling build hook +
`scripts/write-version.py`); see `docs/develop/architecture.md#versioning-and-packaging`.
Never bump the version by editing `_version.py` by hand — run
`uv run scripts/write-version.py` (or a build) and commit the regenerated file
along with the `pyproject.toml` bump.

## Before a release

1. Merge reviewed changes to `main`.
2. Confirm CI is green across all runner OSes.
3. Write human-curated release notes on the tag's GitHub Release.
4. Bump `version` in `pyproject.toml` to `MAJOR.MINOR.PATCH`.
5. Regenerate the version module: `uv run scripts/write-version.py`, then commit
   the regenerated `src/comic_dl/_version.py`.
6. Commit with `git commit -S -m "chore: release v1.4.0"`.

## Cut the tag

```bash
git tag -s v1.4.0 -m "Release 1.4.0"
git push origin v1.4.0
```

Tags and commits are GPG-signed (signing key `D784B9E3D5FA85D2`), so the
matching public key must be registered on the GitHub account for the tag to
display as Verified.

Pushing a `v*` tag triggers `release.yml`:

1. **package** — asserts the tag matches `pyproject.toml` (and the committed
   `_version.py`) via a tag/version gate, then builds sdist + wheel (attached
   to the GitHub Release; PyPI publishing is best-effort until the trusted
   publisher is configured).
2. **linux-packages** — builds `.deb`, `.rpm`, `.pkg.tar.zst` in containers for
   amd64 (plus arm64 for `.deb`/`.rpm`), then **installs each package into a
   fresh container and smoke-tests it** (`--version`, `--list-sources`, `config
   path`, `help`) plus a version-match check and a clean uninstall. A package
   that cannot install blocks the release.
3. **windows-build** — builds `comic-dl.exe` with PyInstaller on a Windows
   runner, runs `--version` (asserted against the tag) and `--help`, and stages
   `comic-dl-<ver>-windows-amd64.{exe,zip}`.
4. **release** — downloads all artifacts, writes `SHA256SUMS`, attaches every
   file to the tag's GitHub Release.

The `packaging.yml` workflow runs the same builds + install/uninstall smoke on
pull requests that touch packaging so breakage is caught before the tag.

## Supported platforms

Every release ships artifacts for:

| Platform | Package | Architectures |
| :------- | :------ | :------------ |
| Debian/Ubuntu | `.deb` | amd64 (`x86_64`), arm64 (`aarch64`) |
| Fedora/RHEL | `.rpm` | amd64, arm64 |
| Arch Linux | `.pkg.tar.zst` | amd64 |
| Windows | `.exe` + `.zip` | amd64 |

The sdist + wheel attached to each GitHub Release (`py3-none-any`) cover every
other case.

Deliberately **not** published:

- **macOS binaries** — the app is planned there, but Apple requires signing +
  notarization before distributing a GUI `.app`; the CI job stays commented out
  until that lands (see `release.yml`). Building from source works on macOS
  today.
- **Native ARM64 Windows** — needs the `windows-11-arm` preview runner + a
  release asset pipeline; amd64 runs under emulation in the meantime.
- **Android** — pure-Python; run under Termux instead. No CI job is planned.

These are extension points, not promises: the commented-out jobs in
`.github/workflows/release.yml` are the reference implementation to flip on
when each becomes feasible.

## Beta gate

The repository was local-only until the beta release gate was green:

1. Plugin contract is stable (`comic_dl.sources` interface final).
2. Full gate is green (`pytest`, `ruff`, `mypy`, `bandit`).
3. `comic-dl --list-sources` enumerates built-in + plugin sources.
4. Docs are present and accurate.
5. No known security regressions.

This gate is cleared for `0.0.1`. Re-check this
list before every later release.

## Documentation artifacts

Documentation is plain Markdown under `docs/` — it is the source of truth.
Release notes link to `docs/` pages directly.

## Release notes

`CHANGELOG.md` at the repo root is the changelog — it tracks every release and
is updated with each tag. For each release:

1. Write the curated `CHANGELOG.md` entry (group by Added / Changed /
   Deprecated / Removed / Fixed / Security, link to relevant `docs/` pages).
2. GitHub auto-generates a draft release from merged PR titles/labels.
3. Edit the draft to match the `CHANGELOG.md` entry, drop trivial commits.
4. Publish the release once CI artifacts are attached.

## Code signing

The Windows `comic-dl.exe` is currently **unsigned**. Authenticode signing
and macOS notarization are future enhancements.
