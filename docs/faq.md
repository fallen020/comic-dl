# Frequently Asked Questions

## What is comic-dl?

A command-line downloader for comic and manga galleries. You give it a series
URL, choose your chapters, and it fetches every page and packs them into CBZ,
ZIP, or CBT archives with embedded metadata.

## Which readers can open the output?

Any reader that accepts CBZ or ZIP archives. A `.cbz` is just a ZIP containing
the page images and a `ComicInfo.xml`, which is why the extraction-friendly
flavors — Tachiyomi, Komikku, Calibre — all work. The ZIP format carries the
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

The full per-platform table — exact filenames, webview prerequisites, and how
to update each install — is in [Installation](install.md).

### Which Python version is required?

3.11 or later. Verify before a source build:

```bash
python3 --version
```

### Updating

Replace your binary with the newer release, or for a source checkout:

```bash
cd comic-dl
git pull
uv sync
```

See [Update and uninstall](install.md#update-and-uninstall) for the
binary-by-binary instructions.

## Downloading

### Downloading a series

Point `-u` at a series page. With no `--chapters`, comic-dl drops you into an
interactive picker so you can choose before anything downloads:

```bash
comic-dl -u "https://www.webtoons.com/en/romance/little-momma/list?title_no=1234"
```

That URL is illustrative — substitute a real one. To skip the picker and grab
everything:

```bash
comic-dl -u <series-url> --chapters all
```

`--chapters` also accepts ranges and lists (`"1-3,7"`; `0` selects a
prologue/promo episode).

### Downloading several series at once

Create a text file with one URL per line and pass it with `-f`:

```bash
comic-dl -f urls.txt
```

### Resuming an interrupted download

Just re-run the same command. Chapters that finished are skipped, and a
chapter whose pages failed is written with a `.cbz.partial` marker — the next
run retries only those missing pages and repairs it. This happens without
`--force`; seeing `Download incomplete` and a `(N partial)` count in the
run summary is normal and non-destructive.

### Redownloading a finished chapter

By default comic-dl never overwrites an existing archive. Use `--force` to
re-fetch and replace it anyway:

```bash
comic-dl -u <series-url> --chapters all --force
```

### Previewing before you commit

`--dry-run` walks the URLs, lists what would download, and stops before
any bytes move:

```bash
comic-dl -f urls.txt --dry-run
```

## Configuration

The config file lives at `~/.config/comic-dl/config.toml` on Linux,
`~/Library/Application Support/comic-dl/config.toml` on macOS, and
`%APPDATA%\comic-dl\config.toml` on Windows.

`comic-dl config init` writes a populated template (it refuses to overwrite
an existing file). `comic-dl config show` prints the effective configuration —
what defaults plus your file actually resolve to — and `comic-dl config path`
tells you which file it read.

## Troubleshooting

### `Unsupported URL`

No scraper matches, or the fallback couldn't extract anything. comic-dl
attempts a generic scraper on unknown hosts unless you pass `--no-generic`
(or set `[download] generic = false`), so this error means the fallback ran
and came up empty. Check the [supported sites](reference/supported-sites.md)
list first, then consider a plugin.

### Downloads are slow

Per-chapter page downloads run at concurrency 5 by default. If a host starts
throttling you, `-c`/`--concurrency` lower it per run. comic-dl also paces
requests per host (on by default) with limits you can tune — see
[Rate limiting](configure/rate-limiting.md). Defaults are documented in
[config.md](configure/config.md).

### Images come out corrupt, or don't download at all

Sites behind Cloudflare may need a challenge solver; `--solver auto` is the
default and tries the system webview, falling back to TLS impersonation. On
Linux the webview path needs PyGObject and WebKitGTK installed system-wide —
the [webview requirements](configure/cookies.md#webview-requirements) section
lists exact packages. On a headless box, skip the webview entirely with
`--solver impersonation`.

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

Contributing instructions — setup, tests, PR workflow — live in
[CONTRIBUTING.md](https://github.com/fallen020/comic-dl/blob/main/CONTRIBUTING.md).

To support a new site, write a scraper plugin. The
[Writing a Plugin](usage/write-plugin.md) guide walks through the class, the
registration, and the tests; [Plugins](usage/plugins.md) covers loading and
installing third-party ones.
