# comic-dl

**comic-dl** downloads comic and manga galleries from supported websites and
compiles them into CBZ, ZIP, and CBT archives. It automates the complete
workflow — scraping metadata, downloading images with retries and rate limiting,
verifying file integrity, and packaging everything with ComicInfo.xml metadata.

## Key features

- **12 built-in sources** — e-hentai, WEBTOON, MangaDex, Kagane, and more
- **Plugin system** — add any site as a Python plugin, no fork required
- **Concurrent downloads** — parallel page images, batch URLs, and multi-chapter downloads
- **Resumable** — interrupted downloads resume via Range requests; partial files are never lost
- **Safe by default** — SSRF-guarded requests, magic-byte image verification, atomic archive writes
- **Rate limiting** — per-site throttling with configurable overrides
- **Standard formats** — CBZ, ZIP, and CBT archives with embedded ComicInfo.xml metadata

## Install

Download a binary from [GitHub Releases](https://github.com/fallen020/comic-dl/releases)
(no Python needed), or build from source:

```bash
git clone https://github.com/fallen020/comic-dl
cd comic-dl
uv sync
```

See [Installation](install.md) for the full instructions.

## Quick start

```bash
comic-dl -u https://e-hentai.org/g/3161202/e7a26f9e16/
```

Output lands in `~/Downloads/comic-dl/<Series Title>/<Chapter Title>.cbz`.
See [Quick Start](quick-start.md) for a walkthrough.

## Documentation

| Section | For whom | What it covers |
| :------ | :------- | :------------- |
| [Installation](install.md) | Everyone | Install methods: binaries, source |
| [Quick Start](quick-start.md) | New users | From zero to first download in three steps |
| [Downloading](usage/download.md) | Users | Single URL, file batches, interactive mode |
| [Library Management](usage/library.md) | Users | List, info, latest, update, remove, restore |
| [Output & Archives](usage/output.md) | Users | Directory layout, formats, compression |
| [Metadata](usage/metadata.md) | Users | ComicInfo.xml field mapping per site |
| [Plugins](usage/plugins.md) | Users | Finding and installing plugin sources |
| [Writing a Plugin](usage/write-plugin.md) | Developers | Building a scraper plugin |
| [Configuration](configure/config.md) | Users | Config format, locations, per-site options |
| [CLI Reference](reference/cli.md) | Everyone | All commands, flags, and options |
| [Supported Sites](reference/supported-sites.md) | Users | Built-in sources, URL patterns, notes |
| [Troubleshooting](troubleshooting.md) | Users | Common errors and how to fix them |
| [Architecture](develop/architecture.md) | Contributors | Design, data flow, security posture |

## Project links

- [Source code](https://github.com/fallen020/comic-dl)
- [Releases](https://github.com/fallen020/comic-dl/releases)
- [Contributing](https://github.com/fallen020/comic-dl/blob/main/CONTRIBUTING.md)
- [Security policy](https://github.com/fallen020/comic-dl/blob/main/SECURITY.md)
