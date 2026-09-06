# Troubleshooting

Common errors and how to fix them.

## Download errors

### `Access blocked (403)`

The site is blocking automated requests. Possible fixes:

- The webview solver may help: `--solver auto`
- Try a different impersonation profile: `--impersonate chrome131`
- The site may have changed its anti-bot measures — check for updates

### `Not found (404)`

The URL is dead or mistyped. Verify the URL works in a browser.

### `Rate limited (429)`

Too many requests. Possible fixes:

- Lower `--concurrency` (default is 5)
- Wait before retrying
- Add or increase rate limits in config: `[http] rate`

### `Unavailable (451)`

Content unavailable for legal reasons. Nothing can be done.

### `Gallery has no images`

The gallery is private, deleted, or requires login (common with e-hentai).
Try obtaining a `cf_clearance` cookie via the webview solver.

### `No images found on page`

The post has no visible file attachments (common with pawchive).

### `e-hentai API error`

Expunged gallery, invalid token, or IP ban. Check the gallery URL and try
again later.

### `Unsupported URL`

No scraper handled the URL. By default comic-dl tries the generic fallback on
unknown hosts; this error appears when that is disabled (`--no-generic`) or
cannot extract anything. Check the [supported sites](reference/supported-sites.md)
list, or install a plugin that handles the site.

### `ModuleNotFoundError`

Virtual environment not ready. Run `uv sync`.

### `Permission denied`

Output directory not writable. Check permissions or use `--output` to point
at a different directory.

### `No webview available`

The webview solver needs a platform backend to start:

- On Windows and macOS the system webview is built in.
- On Linux, install PyGObject and WebKitGTK from your distro's package manager.

On headless systems (no display), run under `xvfb-run` or use
`--solver impersonation` instead.

## Retry behavior

Transient errors (429, 500, 502, 503, 504, 509) and network timeouts are
retried automatically with exponential backoff (2/4s with ±20% jitter), capped
at a total of 3 attempts (2 retries) per download. A response serving HTML
where an image belongs (a throttle page) is also retried.

## Batch failures

When downloading from a file, failures continue processing. A summary is
printed at the end with succeeded/skipped/failed counts. The exit code is `1`
when any URL failed.

## Interrupted downloads

Press Ctrl-C to stop gracefully. The current download finishes, partial
`.part` files are kept, and a resume hint is printed. Re-run the same command
to resume.

Resume appends from the byte offset recorded in the `.part` file. The remote
is not asked whether the file changed since the partial was written (no
conditional request), so if a site replaced an image mid-interruption the
resumed copy could stitch old and new bytes together. This is rare; delete
the affected `.part`/page and re-run if a single page ever looks corrupted
after resuming.

## Debugging

Use `-vvv` for full diagnostic output:

```bash
comic-dl -u <URL> -vvv
```

Or redirect diagnostics to a file:

```bash
comic-dl -u <URL> -vvv --debug-file debug.log
```

Set `COMIC_DL_TRACE_HTTP=1` to see HTTP headers at `-vv` without the full
`-vvv` noise.

## Platform-specific issues

### Windows console encoding

The CLI uses Unicode glyphs. Use Windows Terminal, or run `chcp 65001` in
classic `cmd.exe` first.

### Windows long paths

Deep series trees can exceed `MAX_PATH`. Enable the system long-path policy
or keep the output directory short.

### macOS

macOS binaries are not distributed yet — build from source instead (`git
clone` + `uv sync`, see [Installation](install.md)). A standalone binary needs
signing and notarization first.

## Getting help

- **Questions** — [Discussions](https://github.com/fallen020/comic-dl/discussions)
- **Bugs** — [Issues](https://github.com/fallen020/comic-dl/issues/new?template=bug_report.yml)
  (run with `-vvv` and include the traceback)
- **Security** — [Security advisory](https://github.com/fallen020/comic-dl/security/advisories/new)
