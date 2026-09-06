# Quick Start

Go from zero to a downloaded comic in three steps.

## 1. Install

Download the latest binary for your platform from
[GitHub Releases](https://github.com/fallen020/comic-dl/releases), or build
from source:

```bash
git clone https://github.com/fallen020/comic-dl
cd comic-dl
uv sync
```

The commands below assume a binary install (`comic-dl` on PATH). From a source
checkout use `uv run python -m comic_dl` instead. See
[Installation](install.md) for details.

## 2. Download a gallery

```bash
comic-dl -u https://e-hentai.org/g/3161202/e7a26f9e16/
```

## 3. Find your files

Output lands in:

```text
~/Downloads/comic-dl/
  <Series Title>/
    ComicInfo.xml       # series metadata
    cover.jpg           # series cover
    <Chapter>.cbz       # chapter archive with embedded ComicInfo.xml
```

Override the output directory with `--output`:

```bash
comic-dl -u https://e-hentai.org/g/3161202/e7a26f9e16/ -o ~/Comics
```

## Other ways to provide URLs

### From a file

Create a text file with one URL per line:

```text
# My weekend batch
https://e-hentai.org/g/123/abc/
https://pawchive.pw/patreon/user/456/post/789/
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

1. **Resolve** — the URL is matched to a scraper, metadata is fetched
2. **Download** — page images stream to disk with concurrency (default 5 parallel)
3. **Verify** — magic-byte validation rejects non-image responses
4. **Archive** — images are packed into a CBZ with ComicInfo.xml metadata
5. **Library** — the download is recorded for skip/resume on future runs

## Next steps

- [Downloading](usage/download.md) — all download options and flags
- [Configuration](configure/config.md) — persist your preferences
- [Supported Sites](reference/supported-sites.md) — what sources are available
- [CLI Reference](reference/cli.md) — every command and flag
