# Output & Archives

## Directory layout

```
<output-dir>/                    # --output (default: ~/Downloads/comic-dl)
  <Series Title>/                # one folder per series
    ComicInfo.xml                # series-level metadata
    cover.jpg                    # series cover (when available)
    <Chapter Title>.cbz          # one archive per chapter
  .comic-dl/
    library.db                   # SQLite download history
  .nomedia                       # Android gallery exclusion marker
```

## Archive formats

Choose the container with `--format` or `[archive] format` in config:

| Format | Extension | Container | Notes |
| :----- | :-------- | :-------- | :---- |
| `cbz` | `.cbz` | ZIP | Default. Most widely supported by comic readers. |
| `zip` | `.zip` | ZIP | Plain zip. Still embeds ComicInfo.xml. |
| `cbt` | `.cbt` | TAR | Never compressed. `tar -tf` to list, `tar -xf` to extract. |

Switching formats never produces a duplicate chapter — existing downloads of
any format count as "already downloaded."

## Compression

Control compression with `--compress` or `[archive] compression`:

| Mode | Effect |
| :--- | :----- |
| `stored` | No compression (default). Fastest; pages are written as-is. |
| `deflate` | Zlib deflate at default level. ZIP formats only. |
| `deflate:0-9` | Deflate at a specific level (0=fastest, 9=smallest). |

Comic pages are already-compressed raster images, so deflate rarely shrinks
the archive. The default is deliberately `stored`; compression is opt-in.

`.cbt` (tar) archives are never compressed.

## Inside the archive

- **Page images** — numbered sequentially: `Page_0001.jpg`, `Page_0002.png`, …
- **ComicInfo.xml** — chapter-level metadata: series, chapter title, number,
  volume, page count, source URL.

Series-level metadata (description, creators, genres, rating) lives in the
folder-level `ComicInfo.xml` beside the archives, not inside them.

See [Metadata](metadata.md) for the full field mapping.

## Duplicate detection

SHA-256 dedup runs inside each archive. When multiple pages share the same
byte size, only the first page with a given hash is kept. The rest are
skipped. This is deterministic — the first page always wins.

## Atomic writes

Archives are written to a `.tmp` file, verified (`testzip()` for ZIP, full
member read for TAR), then atomically renamed. A partial archive is never
left behind on disk.

## Partial downloads and resume

If some pages fail (e.g. a temporary 404), the chapter is saved as a partial
CBZ. A `<chapter>.cbz.partial` marker is written next to it and the run exits
non-zero. Partial chapters are reported separately from finished ones — the
end-of-run summary shows a `Download incomplete`/`Download completed with
errors` verdict and a `(N partial)` count rather than folding them into
`failed`.

The next run treats that chapter as *not* downloaded and retries just the
missing pages, overwriting the partial file. Re-running without `--force`
repairs incomplete chapters.

## Text-only galleries

Galleries with no images are saved as `<Chapter Title>.md` (Markdown) with a
`<!-- source: URL -->` marker as the first line.

## FSIComics folder grouping

FSIComics chapter URLs containing a `-chapter-` marker in the slug are
grouped under a `Series Title – Artist` folder. Single-chapter downloads keep
the plain `Series Title` folder.

## Removed series

Removed series move to `<output>/.comic-dl/trash/` and are purged after 7
days.

## Temporary files

Scratch space is created under your platform temp directory during each run
and cleaned up automatically on completion or interruption:

| Platform | Location |
| :------- | :------- |
| Linux/macOS | `/tmp/comic-dl/` |
| Windows | `%TEMP%\comic-dl\` |
