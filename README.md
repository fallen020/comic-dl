# comic-dl

[![CI](https://github.com/fallen020/comic-dl/workflows/CI/badge.svg)](https://github.com/fallen020/comic-dl/actions)
[![Release](https://github.com/fallen020/comic-dl/workflows/Release/badge.svg)](https://github.com/fallen020/comic-dl/releases)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://github.com/fallen020/comic-dl)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Download comics and manga from supported websites from the command line.

comic-dl is a command-line downloader for comics and manga. It is designed to
be simple to use, scriptable, and useful both interactively and from
automation.

## Status

**Early release — 0.0.1.** The project is still evolving. Website support,
command-line options, and configuration may change between releases.

## Features

- Download comics and manga from supported websites
- Command-line interface suitable for interactive use and scripts
- Configurable download locations and behavior
- Resume or retry downloads where supported
- Useful progress and error reporting
- Works on Linux and Windows
- No account or subscription is required unless the source website requires one

Supported websites are listed in the [Supported sites](#supported-sites)
section.

## Installation

### Download a binary

Each GitHub release ships prebuilt packages that need no Python:

- `.deb`, `.rpm`, and `.pkg.tar.zst` for Debian-, Fedora-, and Arch-based
  Linux distributions
- a `comic-dl-<ver>-windows-amd64.zip` (standalone executable) for Windows

Pick the package matching your machine from
[the latest release](https://github.com/fallen020/comic-dl/releases).

### Build from source

Requires Python 3.11 or newer and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/fallen020/comic-dl
cd comic-dl
uv sync
```

Run it from the checkout with `uv run python -m comic_dl --help`, or via the
installed script `.venv/bin/comic-dl`.

After installing either way, verify it:

```bash
comic-dl --version
```

You should see:

```text
comic-dl 0.0.1
```

See [docs/install.md](docs/install.md) for the full instructions.

## Usage

The basic usage is:

```bash
comic-dl -u http://...

For example:

```bash
comic-dl --url "https://example.com/comic/..."
```

Use `--help` to see the options available in your installed version:

```bash
comic-dl --help
```

## Configuration

comic-dl is intended to work with sensible defaults without requiring a
configuration file.

When configuration is available, it can be used to customize things such as:

- download directory
- download behavior
- concurrency
- retries
- output naming

See the project documentation and `comic-dl --help` for the configuration
options supported by the current release.

## Where are files downloaded?

By default, downloads are placed in the system Downloads folder under a
`comic-dl/` directory — for example `~/Downloads/comic-dl/` on Linux. Change
it with `-o <DIR>` / `--output` on the command line or the `output` setting
in the configuration file.

The saved path is printed at the end of each download.

Configuration and output paths are intentionally kept separate from a source
tree so that using comic-dl does not create files inside a cloned repository.

## Supported sites

comic-dl supports websites for which download support has been implemented
and tested.

Supported sites are listed in the project documentation:

- [Supported Sites](docs/reference/supported-sites.md)

Support for a website can break when that website changes its HTML, API,
authentication, or anti-bot mechanisms. This is normal for downloaders that
interact with third-party websites.

If a previously supported site stops working, please open an issue with:

- the website name
- the URL pattern
- the comic-dl version
- the operating system
- the relevant error message

## Troubleshooting

### A website is not working

First check that you are running the latest release.

Then run:

```bash
comic-dl --help
```

and retry the command.

If the problem persists, open an issue and include the version, operating
system, website, and error message.

Do not include private credentials or session information.

### Downloads fail or stop part-way through

Check your network connection and available disk space.

If the problem is reproducible, report it as an issue with enough information
to reproduce the failure.

## Requirements

For the Python installation:

- Python 3.11 or newer
- Internet access to the target website
- Sufficient disk space for downloaded content

Some website integrations may have additional requirements. See the
documentation for details.

## Platform support

| Support | Platforms |
| :------ | :-------- |
| Supported | Debian-based Linux, Fedora-based Linux, Arch-based Linux (amd64 only), Windows |
| Planned | macOS, Android |

These platforms are not supported by the 0.0.1 release. Work may be done on
them in the future, but they should not be considered production-ready.

## Development

Clone the repository:

```bash
git clone https://github.com/fallen020/comic-dl
cd comic-dl
```

Create the development environment with `uv` (see
[docs/develop/setup.md](docs/develop/setup.md)):

```bash
uv sync --extra dev --locked
```

Run the test suite:

```bash
scripts/test.sh
```

Run linting:

```bash
scripts/lint.sh
```

Build the project:

```bash
scripts/build.sh
```

Windows developers should use the corresponding Windows scripts where
provided.

Before submitting changes, make sure the tests and build complete
successfully.

## Contributing

Contributions are welcome.

Before opening a pull request:

- Check existing issues and pull requests.
- Keep changes focused.
- Add or update tests when behavior changes.
- Run the project's test and lint checks.
- Update documentation when user-facing behavior changes.

For larger changes, opening an issue first can help avoid duplicated work.

Please read the project's contribution guidelines if available.

## Reporting bugs

Use the GitHub issue tracker to report bugs.

A useful bug report includes:

- comic-dl version
- operating system
- Python version, if applicable
- website being accessed
- command used, with private information removed
- complete error message
- steps needed to reproduce the problem

Please do not include:

- passwords
- API keys
- cookies
- authentication headers
- browser profiles
- private URLs
- personal filesystem contents

## Responsible use

comic-dl is a tool for downloading content from websites that you are
authorized to access.

Respect the terms of service, robots policies where applicable, copyright
laws, and the rights of content creators and publishers.

The project does not encourage bypassing authentication, paywalls, access
controls, or other technical restrictions.

Users are responsible for how they use the software and for ensuring that
their use complies with applicable laws and the policies of the websites they
access.

## License

comic-dl is free and open-source software.

See [LICENSE](LICENSE) for the full license text.

Third-party components and dependencies remain subject to their respective
licenses; see [Third-Party-Licenses](Third-Party-Licenses/README.md).

## Releases

Released versions are published on GitHub.

The 0.0.x series is an early release and may contain breaking changes as the
project matures.

For reproducible installations, prefer a numbered release over installing
directly from the development branch.

## Project status

comic-dl is currently in early development.

The goals for the project are straightforward:

- make downloading supported comics and manga easy
- keep the command-line interface predictable
- avoid unnecessary complexity
- make failures understandable
- keep the project usable from scripts and automation

If something does not work, please report it. Bug reports and contributions
help determine what gets fixed and supported next.

comic-dl — download comics from the command line.
