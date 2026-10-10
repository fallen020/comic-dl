# Frequently Asked Questions

## What is comic-dl?

A command-line downloader for comic and manga galleries. You give it a series
URL, choose your chapters, and it fetches every page and packs them into CBZ,
ZIP, or CBT archives with embedded metadata.

## Which readers can open the output?

Any reader that accepts CBZ or ZIP archives. A `.cbz` is just a ZIP containing
the page images and a `ComicInfo.xml`, which is why the extraction-friendly
flavors (Tachiyomi, Komikku, Calibre) all work. The ZIP format carries the
same metadata as CBZ; CBT is a TAR container (uncompressed) meant more for
interop than for nice file managers.

## Is comic-dl free?

Yes, under the MIT license.

## Installation

### Do I need Python?

Not if you install a prebuilt binary; on Windows the standalone `.exe`
bundles the native `curl-cffi` runtime too, so there's nothing else to
install. Linux packages (deb/rpm/Arch) work the same way. Only building from
source requires Python 3.11+ and `uv`. macOS ships no binary, so source is
the only route there.

The per-platform table, with exact filenames, webview prerequisites, and how
to update each install, is in [Installation](install.md).

### Which Python version is required?

3.11 or later. Verify before a source build:

```bash
python3 --version
```

### How do I update?

Replace your binary with the newer release, or for a source checkout:

```bash
cd comic-dl
git pull
uv sync
```

See [Update](install.md#update) for the binary-by-binary instructions.

## Downloading

### Downloading a series

Point `-u` at a series page. With no `--chapters`, comic-dl drops you into an
interactive picker so you can choose before anything downloads:

```bash
comic-dl -u "https://www.webtoons.com/en/romance/demo-series/list?title_no=1"
```

That URL is illustrative, so substitute a real one. To skip the picker and grab
everything:

```bash
comic-dl -u <series-url> --chapters all
```

`--chapters` also accepts ranges and lists:

- `"1-3,7"` — chapters 1, 2, 3, and 7
- `"1-3,7,10-"` — chapters 1, 2, 3, 7, and 10 through the end
- `"all"` — all chapters
- `"0"` — prologue/promo only

### Downloading several series at once

Create a text file with one URL per line and pass it with `-f`:

```bash
comic-dl -f urls.txt
```

### Resuming an interrupted download

Just re-run the same command. Chapters that finished are skipped, and a
chapter whose pages failed is written with a `.cbz.partial` marker, so the next
run retries only those missing pages and repairs it. This happens without
`--force`; seeing `Download incomplete` and a `(N partial)` count in the
run summary is normal and non-destructive.

### Redownloading a finished chapter

By default comic-dl never overwrites an existing archive. Use `--force` to
re-fetch and replace it anyway:

```bash
comic-dl -u <series-url> --chapters all --force
```

### Machine-readable output (`--json`)

`--json` prints a structured JSON payload on **stdout only**. All human-facing
messages (progress, warnings, errors, summaries) go to **stderr**. That keeps
the JSON stream clean for scripting: pipe stdout to `jq` or a parser while
stderr still shows live progress.

```bash
comic-dl -u <URL> --json | jq .
```

The schema version is included in every response (`"schema_version": 2`).

### Previewing before you commit

`--dry-run` fetches metadata and may probe image sizes to list what would
download, without creating chapter archives:

```bash
comic-dl -f urls.txt --dry-run
```

### Is it legal to download with comic-dl?

comic-dl only serves you links a site has already made public, and it does
not bypass paywalls or access controls by default. Whether a particular
download is legal depends on your jurisdiction, the site's terms, and the
specific work. See [Legal](legal.md) for comic-dl's stance and for how
similar tools have fared. [Privacy](privacy.md) explains what comic-dl
stores locally and sends over the network.

## Configuration

The config file lives at `~/.config/comic-dl/config.toml` on Linux,
`~/Library/Application Support/comic-dl/config.toml` on macOS, and
`%LOCALAPPDATA%\comic-dl\config.toml` on Windows.

`comic-dl config init` writes a populated template (it refuses to overwrite
an existing file). `comic-dl config show` prints the effective configuration,
which is what the defaults plus your file actually resolve to, and
`comic-dl config path` tells you which file it read.

## Troubleshooting

Every download failure has a section on the
[troubleshooting page](troubleshooting.md), with what comic-dl already tried,
what to do first, and when to stop. Three come up most often:

- [`Unsupported URL`](troubleshooting.md#unsupported-url) means no scraper
  matched. comic-dl falls back to a generic scraper on unknown hosts unless you
  pass `--no-generic`, so this error means that fallback came up empty. Check
  the [supported sites](reference/supported-sites.md) list, then consider
  [writing a plugin](usage/write-plugin.md).
- [Downloads are slow](configure/rate-limiting.md): lower `--concurrency` for
  one run, or set a per-host rate in the config for good.
- [Images come out corrupt, or don't download at all](configure/cookies.md)
  usually means the site sits behind Cloudflare. The default
  `--solver off` fails it with a hint instead of retrying; enable
  `--solver auto` when you want it retried through the system webview.

### Getting more output for diagnosis

`-vvv` prints the request/response diary:

```bash
comic-dl -u <series-url> -vvv
```

### Reporting a bug

Reproduce with `-vvv`, then open an
[issue](https://github.com/fallen020/comic-dl/issues/new?template=bug_report.yml)
and paste the traceback, the exact command, and the release version.

## Development

Contributing instructions live in
[CONTRIBUTING.md](https://github.com/fallen020/comic-dl/blob/main/CONTRIBUTING.md):
setup, tests, and PR workflow.

To support a new site, write a scraper plugin. The
[Writing a Plugin](usage/write-plugin.md) guide walks through the class, the
registration, and the tests; [Plugins](usage/plugins.md) covers loading and
installing third-party ones.
