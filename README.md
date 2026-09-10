# comic-dl

[![CI](https://github.com/fallen020/comic-dl/workflows/CI/badge.svg)](https://github.com/fallen020/comic-dl/actions)
[![Release](https://github.com/fallen020/comic-dl/workflows/Release/badge.svg)](https://github.com/fallen020/comic-dl/releases)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://github.com/fallen020/comic-dl)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Download comics and manga from supported websites and package them into CBZ,
ZIP, or CBT archives.

comic-dl is a command-line downloader designed for both interactive use and
scripting. It scrapes supported sites with built-in adapters and a generic
fallback, fetches galleries page by page under configurable concurrency, and
emits archives ready for your reader.

**Early release (`0.0.x`).** Command-line options, configuration, and site
support may change between releases.

## Features

- Built-in adapters and a generic HTML fallback for unrecognized sites
- Interactive chapter picker, or scriptable `--chapters 1-3,5` selection
- Interrupted downloads resume from the intact pages already on disk
- Per-site rate limiting and shared retry cooldown for polite scraping
- ComicInfo.xml metadata embedded in generated archives
- CBZ, ZIP, and CBT output formats
- Linux and Windows support (macOS planned); no account required unless the
  source site requires one

## Installation

### Prebuilt packages

Every GitHub release ships packages that need no Python:

- `.deb`, `.rpm`, and `.pkg.tar.zst` for Debian-, Fedora-, and Arch-based
  Linux distributions
- a standalone Windows executable (`.zip`)

Pick the artifact matching your machine from
[the latest release](https://github.com/fallen020/comic-dl/releases).

### From source

Requires Python 3.11 or newer and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/fallen020/comic-dl
cd comic-dl
uv sync
```

Run it from the checkout:

```bash
uv run python -m comic_dl --help
```

or through the installed script `.venv/bin/comic-dl`.

Either way, verify the installation:

```bash
comic-dl --version
```

```text
comic-dl 0.0.1
```

See [docs/install.md](docs/install.md) for the full instructions.

## Quick start

```bash
comic-dl --url "https://example.com/series/chapter-1"
```

Point `--url` at a series to open the interactive chapter picker. For
non-interactive use, select chapters by number:

```bash
comic-dl --url "https://example.com/series" --chapters 1-3,5
```

`--help` lists every option in your installed version.

## Where files are saved

Downloads go to the system Downloads folder under `comic-dl/`
(`~/Downloads/comic-dl/` on Linux) unless you change it with `-o <DIR>`/`--output`
or the `output` setting in the config file. The saved path is printed when a
download completes.

## Supported sites

The list of adapted sites lives in
[docs/reference/supported-sites.md](docs/reference/supported-sites.md).

Site support can break when a site changes its HTML, API, authentication, or
anti-bot measures — normal for any downloader that talks to third-party
services. If a site stops working, open an issue with the site name, a URL
pattern, the comic-dl version, your OS, and the error message.

## Configuration

comic-dl works with sensible defaults and needs no config file. When present,
the config file can set the download directory, concurrency, retries, output
naming, and more. See [docs/configure/config.md](docs/configure/config.md) and
`comic-dl --help` for the options in the current release.

## Platform support

| Status | Platforms |
| :----- | :-------- |
| Supported | Debian-, Fedora-, and Arch-based Linux (amd64), Windows |
| Planned | macOS, Android |

## Troubleshooting

- **A site stopped working** — make sure you are on the latest release, then
  retry. Open an issue with the version, OS, site, and error message.
- **Downloads fail or stop partway** — check your network connection and free
  disk space. Report reproducible failures with reproduction steps.

Never include credentials, cookies, or session data in any issue report.

## Development

```bash
uv sync --extra dev --locked
./scripts/test.sh    # full offline test suite
./scripts/lint.sh    # ruff, mypy, bandit, shellcheck
./scripts/build.sh   # sdist + wheel
./scripts/docs.sh    # Markdown lint
```

See [docs/develop/setup.md](docs/develop/setup.md) for requirements. Windows
developers use the corresponding `.ps1` scripts where provided.

## Contributing

Contributions are welcome:

- Check existing issues and pull requests first.
- Keep changes focused; add or update tests for behavior changes.
- Update documentation when user-facing behavior changes.
- Run `./scripts/test.sh` and `./scripts/lint.sh` before submitting.

For larger changes, open an issue first so the work is not duplicated.

## Responsible use

Use comic-dl only for content you are authorized to access. Respect terms of
service, robots policies, copyright law, and the rights of content creators
and publishers. The project does not encourage bypassing authentication,
paywalls, or other access controls, and users are responsible for how the
software is used and for complying with applicable law and site policies.

## License

comic-dl is MIT-licensed — see [LICENSE](LICENSE). Third-party components
remain under their respective licenses
([Third-Party-Licenses](Third-Party-Licenses/README.md)).

## Project status

comic-dl is in early development. The priorities are simple: downloads stay
easy, the CLI stays predictable, failures stay explainable, and the project
remains usable from scripts and automation.

Bug reports and contributions decide what gets fixed and supported next.
