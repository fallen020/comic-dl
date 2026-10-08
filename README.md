<!-- pyml disable md041 -->
<div align="center">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/assets/wordmark-dark.svg">
      <img alt="comic-dl" src="docs/assets/wordmark-light.svg" width="550" height="118">
    </picture>
<br>

<a href="https://github.com/fallen020/comic-dl/actions">
  <img alt="CI" src="https://github.com/fallen020/comic-dl/workflows/CI/badge.svg">
</a>
<a href="https://github.com/fallen020/comic-dl/releases">
  <img alt="Release" src="https://github.com/fallen020/comic-dl/workflows/Release/badge.svg">
</a>
<a href="LICENSE">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square">
</a>

<h4 align="center">
  [<a href=https://fallen020.github.io/comic-dl/docs/installation/>Install</a>]
  [<a href=https://fallen020.github.io/comic-dl/docs/usage/basic/>Usage</a>]
  [<a href=https://fallen020.github.io/comic-dl/docs/reference/supported-sites/>Sites</a>]
  [<a href=https://fallen020.github.io/comic-dl/docs/configure/config/>Configuration</a>]
  [<a href=https://fallen020.github.io/comic-dl/docs/troubleshooting/>Troubleshooting</a>]
  [<a href=https://fallen020.github.io/comic-dl/docs/>Docs</a>]
</h4>

<p align="center">
  <img src="docs/assets/demo.gif" alt="comic-dl downloading selected chapters and saving CBZ files" width="800">
</p>

</div>

**CLI that downloads comic and manga chapters and packs them into CBZ, ZIP, or
CBT with ComicInfo.xml metadata.**

Point comic-dl at a page and it does the rest:

1. **Point** — a chapter URL, a series URL, or a file of URLs.
2. **Pick** — series, chapters, and pages from the picker.
3. **Pack** — SHA-256-verified pages plus `ComicInfo.xml` into a CBZ, ZIP, or CBT archive.

comic-dl is **not** an aggregator and does not bypass authentication, paywalls,
or region locks: it downloads only what the site serves you.

## Why comic-dl?

| Instead of | comic-dl does |
| --- | --- |
| Clicking 30 "next" pages and zipping a folder | One URL, verified and numbered page images |
| An ad-hoc per-site script that breaks on redesigns | 34 built-in scrapers plus a plugin system |
| A folder of loose images with no metadata | Reader-ready CBZ/ZIP/CBT with `ComicInfo.xml` |

Above that, it resumes missing pages, dedupes images by SHA-256, and records
finished chapters in a local SQLite library.

> [!WARNING]
> comic-dl is an early release. Commands, flags, and site support can
> change between releases.

## Install

Download a prebuilt package from [GitHub Releases][release]:

| Platform | Package |
| --- | --- |
| Windows | `.exe` or `.zip` |
| Debian/Ubuntu | `.deb` (`amd64`, `arm64`) |
| Fedora | `.rpm` (`x86_64`, `aarch64`) |
| Arch | `.pkg.tar.zst` (`x86_64`) |
| Any (Python 3.11+) | `.whl` — install with `pip` from the file |
| macOS | no native package — see [docs/install.md](docs/install.md) |

```bash
comic-dl --version
```

Verify the download against the release `SHA256SUMS`; see
[docs/install.md](docs/install.md#verify).

To run from source, install Python 3.11+ and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/fallen020/comic-dl
cd comic-dl
uv sync
uv run comic-dl --version
```

Use `uv run comic-dl` from a source checkout. Full per-OS instructions are in
[docs/install.md](docs/install.md).

> [!WARNING]
> `pip install comic-dl` is not supported; the PyPI name belongs to an
> unrelated project.

## Quick start

```bash
comic-dl -u "<gallery-url>"  # any URL from a supported site
comic-dl --help               # all flags
```

Replace `<gallery-url>` with a URL from a
[supported site](https://fallen020.github.io/comic-dl/docs/reference/supported-sites/).

## Documentation

The [full documentation index](docs/index.md) lists every page by audience.
The ones most readers open first:

- [Usage](docs/usage/download.md) — flags, chapter and page selection, output layout
- [Configuration](docs/configure/config.md) — paths, per-site overrides, cache
- [CLI reference](docs/reference/cli.md) — every command and flag
- [Write a scraper](docs/usage/write-plugin.md) — add a new site
- [Architecture](docs/develop/architecture.md) and [releasing](docs/develop/releasing.md) for contributors

## Contributing and development

Pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Development
setup and the test, lint, build, and documentation gates are documented in
[docs/develop/setup.md](docs/develop/setup.md).

## Responsible use

Download only content you are authorized to access and follow the source
site's terms and applicable law. See the [Legal](docs/legal.md) and
[Privacy](docs/privacy.md) pages for the full terms and data-handling
description.

## License

[MIT](LICENSE). Third-party components retain their own terms; see
[Third-Party-Licenses](Third-Party-Licenses/README.md).

[release]: https://github.com/fallen020/comic-dl/releases/latest
