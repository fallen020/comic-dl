<!-- pyml disable md041 -->
<div align="center">
    <h1>comic-dl</h1>

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
  [<a href="#install">Install</a>]
  [<a href="#usage">Usage</a>]
  [<a href="#supported-sites">Sites</a>]
  [<a href="#configuration">Configuration</a>]
  [<a href="#troubleshooting">Troubleshooting</a>]
  [<a href="#documentation">Docs</a>]
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
| An ad-hoc per-site script that breaks on redesigns | 29 built-in scrapers plus a plugin system |
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

## Usage

The examples use
[Becoming the Cheon Clan's Mad Dog](https://asurascans.com/comics/becoming-the-cheon-clans-mad-dog-05c7df14).
comic-dl is independent. It is not affiliated with, endorsed by, or sponsored
by Asura Scans, and this example is not an endorsement of the service.

Download one chapter:

```bash
comic-dl -u "https://asurascans.com/comics/becoming-the-cheon-clans-mad-dog-05c7df14/chapter/1"
```

Download a series and choose chapters in the picker:

```bash
comic-dl -u "https://asurascans.com/comics/becoming-the-cheon-clans-mad-dog-05c7df14"
```

Download specific source chapter numbers to `./out`:

```bash
comic-dl -u "https://asurascans.com/comics/becoming-the-cheon-clans-mad-dog-05c7df14" \
  --chapters 1-3,5 -o ./out
```

A separate live run selecting chapters 21 and 22 completed in 18 seconds:

```text
Selected 2/22 chapters
  ✔ [21/22] Saved: Chapter 21.cbz (14.6 MB)
  ✔ [22/22] Saved: Chapter 22.cbz (14.4 MB)

  ✔ Download complete

    Series     : Becoming the Cheon Clan's Mad Dog
    Selected   : 2 / 22 chapters
    Downloaded : 2 chapters
    Size       : 29 MB
    Duration   : 18s
    Average    : 1.57 MB/s
    Saved to   : /tmp/opencode/comic-dl-asura-demo/Becoming the Cheon Clan's Mad Dog
```

Without `-o`, archives go to
`~/Downloads/comic-dl/<Series>/<Chapter>.cbz` on Linux. Each finished archive
contains numbered page images and `ComicInfo.xml`; the completed chapter is
also recorded in `.comic-dl/library.db`.

Image requests time out after 60 seconds and receive two retries by default.
Known host rates are 1.5 requests/second for Kagane and 2 requests/second for
Kstatic and E-Hentai; configure any host with `[http].rate`.

## Supported sites

29 built-in scrapers include MangaDex, WEBTOON, E-Hentai, Tapas, WeebCentral,
Asura Scans, and Madara-based sites. The
[supported-sites table](docs/reference/supported-sites.md) lists every accepted
URL shape; `comic-dl --list-sources` also shows installed plugins.

If a scraper breaks, open an issue with the site, URL pattern, comic-dl
version, OS, and error text. Do not include cookies or credentials.

## Configuration

Configuration is optional. Create the file with `comic-dl config init`, then
edit these 8 keys:

```toml
output = "~/Downloads/comic-dl"
concurrency = 5

[http]
solver = "off"
download-retries = 2
rate = { "e-hentai.org" = 2.0 }

[archive]
format = "cbz"
```

CLI flags override config values, and config values override these defaults.
See [docs/configure/config.md](docs/configure/config.md) for paths, per-site
overrides, cache settings, and the complete generated file.

## Troubleshooting

- `ModuleNotFoundError: No module named 'comic_dl'` — run `uv sync` in the
  source checkout.
- `Access blocked (403)` — open the URL in a normal browser. If it loads there,
  retry with `--solver webview`; if the browser is blocked too, comic-dl
  cannot bypass that site or region restriction.

The [troubleshooting guide](docs/troubleshooting.md) covers 403, 404, 429,
missing webviews, interrupted downloads, and debug logs.

## Documentation

- [Usage](docs/usage/download.md) — flags, chapter and page selection, output layout
- [Library](docs/usage/library.md) — the SQLite download history
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
