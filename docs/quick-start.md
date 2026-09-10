# Quick Start

Install comic-dl, download a gallery, and read the archive it produces.

## 1. Install

Download a binary from
[GitHub Releases](https://github.com/fallen020/comic-dl/releases), or build
from source:

```bash
git clone https://github.com/fallen020/comic-dl
cd comic-dl
uv sync
```

The commands below assume `comic-dl` is on your PATH. From a source checkout
replace `comic-dl` with `uv run python -m comic_dl`. See
[Installation](install.md) for platform-specific details.

## 2. Download a gallery

```bash
comic-dl -u <gallery-url>
```

Replace `<gallery-url>` with a gallery or series page from a
[supported site](reference/supported-sites.md).

## 3. Find the output

Output lands in `<Downloads>/comic-dl/`:

| Platform | Default output |
| :------- | :------------- |
| Linux | `~/Downloads/comic-dl/` |
| macOS | `~/Downloads/comic-dl/` |
| Windows | `%USERPROFILE%\Downloads\comic-dl\` |

On Windows this follows the real Shell Folders value, so a OneDrive-redirected
home still resolves correctly. Run `comic-dl config show` to print the exact
directory for your machine.

Inside the output directory, each series gets its own folder:

```text
<Downloads>/comic-dl/
  <Series Title>/
    ComicInfo.xml       # series metadata
    cover.jpg           # series cover
    <Chapter>.cbz       # chapter archive with embedded ComicInfo.xml
```

Override the output directory with `--output`:

```bash
comic-dl -u <gallery-url> -o ~/Comics
```

## Other ways to provide URLs

### From a file

Create a text file with one URL per line:

```text
# My weekend batch
https://e-hentai.org/g/<gid>/<token>/
https://pawchive.pw/patreon/user/<id>/post/<id>/
```

```bash
comic-dl -f urls.txt
```

Blank lines, `#` comments, and inline `# comments` after URLs are supported.
Duplicate URLs are downloaded once.

### Interactive mode

Run without arguments to be prompted for input:

```bash
comic-dl
```

## What happens during a download

1. **Resolve** — match the URL to a scraper and fetch metadata
2. **Download** — fetch page images concurrently, 5 at a time by default
3. **Verify** — check that each downloaded response is actually an image
4. **Archive** — pack the images into a CBZ with a `ComicInfo.xml`
5. **Record** — save the download in the library so future runs can skip it

## Next steps

- [Downloading](usage/download.md) — all download options and flags
- [Configuration](configure/config.md) — persist your preferences
- [Supported Sites](reference/supported-sites.md) — what sources are available
- [CLI Reference](reference/cli.md) — every command and flag
