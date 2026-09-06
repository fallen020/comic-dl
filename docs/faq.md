# Frequently Asked Questions

## General

### What is comic-dl?

comic-dl is a command-line tool that downloads comic and manga galleries from
supported websites and compiles them into CBZ, ZIP, and CBT archives with
embedded metadata.

### Is comic-dl free?

Yes. comic-dl is open source under the MIT License.

### Which comic readers are compatible?

Any reader that supports CBZ/ZIP archives, including
Tachiyomi, Komikku, Calibre, and others.

## Installation

### Do I need Python?

Not if you use a prebuilt binary from
[GitHub Releases](https://github.com/fallen020/comic-dl/releases). The
Windows binary bundles everything including the native `curl-cffi` runtime.

### Which Python version is required?

Python 3.11 or later.

### How do I update?

Download the latest binary from
[GitHub Releases](https://github.com/fallen020/comic-dl/releases). For a
source build, update the checkout and re-sync:

```bash
cd comic-dl
git pull
uv sync
```

## Usage

### How do I download a series (all chapters)?

Pass a series URL:

```bash
comic-dl -u https://www.webtoons.com/en/romance/little-momma/list?title_no=1234
```

comic-dl will present an interactive chapter picker. To download all chapters
without the picker:

```bash
comic-dl -u <series-url> --chapters all
```

### Can I download multiple URLs at once?

Create a text file with one URL per line and pass it with `-f`:

```bash
comic-dl -f urls.txt
```

### How do I resume an interrupted download?

Re-run the same command. comic-dl skips already-downloaded chapters and resumes
partial downloads automatically.

### What happens if a download fails mid-chapter?

The partial chapter is saved with a `.partial` marker. The next run retries
just the missing pages.

### Can I preview what would download?

```bash
comic-dl -f urls.txt --dry-run
```

### How do I force re-download?

```bash
comic-dl -u <URL> --force
```

## Configuration

### Where is the config file?

| Platform | Path |
| :------- | :--- |
| Linux | `~/.config/comic-dl/config.toml` |
| macOS | `~/Library/Application Support/comic-dl/config.toml` |
| Windows | `%APPDATA%\comic-dl\config.toml` |

### How do I create a config file?

```bash
comic-dl config init
```

### How do I see my current config?

```bash
comic-dl config show
```

## Troubleshooting

### I get `Unsupported URL`

No scraper handled the URL. The generic fallback tries unknown hosts by
default; this error appears when it is disabled (`--no-generic`) or cannot
extract content. Check the [supported sites](reference/supported-sites.md)
list, or install a plugin.

### Downloads are slow

Lower `--concurrency` if you're being throttled, or check your rate limit
settings. The default concurrency is 5.

### Images are corrupt or not downloading

Try `--solver auto` for sites with Cloudflare protection. On Linux, make sure
PyGObject and WebKitGTK are installed (see
[Webview requirements](configure/cookies.md#webview-requirements)).

### How do I get more diagnostic output?

```bash
comic-dl -u <URL> -vvv
```

### How do I report a bug?

Run your command with `-vvv`, then open an
[issue](https://github.com/fallen020/comic-dl/issues/new?template=bug_report.yml)
with the traceback.

## Development

### How do I contribute?

See [CONTRIBUTING.md](https://github.com/fallen020/comic-dl/blob/main/CONTRIBUTING.md)
for setup, test, and PR instructions.

### How do I add support for a new site?

Write a scraper plugin. See [Writing a Plugin](usage/write-plugin.md).
