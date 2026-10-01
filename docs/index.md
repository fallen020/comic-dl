# comic-dl documentation

**comic-dl** downloads comic and manga galleries from supported websites and
compiles them into CBZ, ZIP, and CBT archives. It handles the whole workflow:
scraping metadata, downloading images with retries and rate limiting,
verifying file integrity, and packaging everything with ComicInfo.xml metadata.

Start with [Installation](install.md), then [Quick Start](quick-start.md) for
your first download.

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
| [Rate Limiting](configure/rate-limiting.md) | Users | Per-host request pacing |
| [Cookies & Anti-Bot](configure/cookies.md) | Users | Cookie jar, solver modes, webview needs |
| [Environment Variables](configure/environment.md) | Users | Overrides for paths and behavior |
| [CLI Reference](reference/cli.md) | Everyone | All commands, flags, and options |
| [Exit Codes](reference/exit-codes.md) | Everyone | What each status code means |
| [Supported Sites](reference/supported-sites.md) | Users | Built-in sources, URL patterns, notes |
| [Site Support](usage/site-support.md) | Users | Per-adapter versions and live checks |
| [Version and Updates](usage/self-update.md) | Users | `comic-dl self` and its update paths |
| [Troubleshooting](troubleshooting.md) | Users | Decision trees for failed downloads |
| [FAQ](faq.md) | Everyone | Short answers and where to read more |
| [Development Guide](develop/index.md) | Contributors | Reporting, workflow, setup, release |
| [Architecture](develop/architecture.md) | Contributors | Design, data flow, security posture |

## Project links

- [Source code](https://github.com/fallen020/comic-dl)
- [Releases](https://github.com/fallen020/comic-dl/releases)
- [Contributing](https://github.com/fallen020/comic-dl/blob/main/CONTRIBUTING.md)
- [Security policy](https://github.com/fallen020/comic-dl/blob/main/SECURITY.md)
- [Legal](legal.md)
- [Privacy](privacy.md)
