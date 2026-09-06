# Downloading

This page covers every way to download content with comic-dl.

## Download a single URL

```bash
comic-dl -u https://e-hentai.org/g/3161202/e7a26f9e16/
```

The `-u` / `--url` flag accepts any URL from a [supported site](../reference/supported-sites.md).

## Download from a file

Create a text file with one URL per line:

```
# My comics
https://e-hentai.org/g/123/abc/       # weekend batch
https://pawchive.pw/patreon/user/456/post/789/
```

```bash
comic-dl -f urls.txt
```

Blank lines and lines starting with `#` are skipped. Inline `# comments` after
a URL are stripped. Duplicate URLs are downloaded once. Lines that are not
`http(s)://` URLs produce a warning naming the file and line.

If any URL fails, processing continues and a summary is printed at the end.
The exit code is `1` when one or more downloads fail. Each error is annotated
with its origin (e.g. `Failed: https://... (urls.txt:12)`).

## Interactive mode

Run without arguments to be prompted for input:

```bash
comic-dl
```

The tool accepts either a gallery URL or a text file path. Requires a TTY;
scripts must use `-u` or `-f`.

## Custom output directory

```bash
comic-dl -u <URL> -o ~/Comics
```

Default: `~/Downloads/comic-dl/`.

## Chapter selection

When a URL points at a series (multiple chapters), comic-dl presents an
interactive chapter picker by default. To skip the picker:

```bash
comic-dl -u <series-url> --chapters all       # download everything
comic-dl -u <series-url> --chapters 1-3,5     # specific chapters
comic-dl -u <series-url> --chapters 7          # a single chapter
```

`--chapters` only accepts numbers and ranges. The `q`/`quit` cancel token is
reserved for the interactive picker.

## Size limits

### Per-image limit

```bash
comic-dl -u <URL> --max-image-size 50MB
```

Default: `100 MB`. Images exceeding this are rejected. Accepts suffixed values
(`500 KB`, `1 GB`) or plain bytes.

### Per-run limit

```bash
comic-dl -u <URL> --max-size 2GB
```

Default: `0` (unlimited). When an estimate exceeds this limit, the chapter is
counted as failed (exit code `1`) but other URLs continue.

## Re-downloading

By default, already-downloaded chapters are skipped. To force a re-download:

```bash
comic-dl -u <URL> --force
```

`--force` bypasses both the on-disk check and the library index, overwriting
existing files.

When `--force` is combined with a multi-URL file and re-downloads would occur,
comic-dl asks for confirmation first. Non-interactive runs (`--json`) refuse
unsolicited with exit code `130` (same class as an unanswerable confirmation
prompt).

## Dry run

Preview what a run would do without writing anything:

```bash
comic-dl -f urls.txt --dry-run
comic-dl -f urls.txt --force --dry-run    # preview re-downloads
comic-dl -f urls.txt --dry-run --json     # machine-readable preview
```

`--dry-run` resolves metadata asynchronously and reports the action
(`download`, `skip`, or `redownload`), archive destination, and estimated
total. No writes are made and no prompts are shown.

## Quiet mode

Suppress all output except errors:

```bash
comic-dl -f urls.txt --quiet
```

Useful for scripts and cron jobs.

## JSON output

Machine-readable output for scripting:

```bash
comic-dl -u <URL> --json
comic-dl -f urls.txt --json
comic-dl list --json
comic-dl info <series> --json
comic-dl latest --json
```

`--json` implies non-interactive mode. All human output goes to stderr; stdout
receives only the JSON payload. Every payload includes a `schema_version`
field (currently `1`).

See [CLI Reference](../reference/cli.md#json-output) for the full JSON schema.

## Verbosity

Control diagnostic output with `-v` flags:

| Level | Flag | Shows |
| :---- | :--- | :---- |
| 0 | (default) | Progress, status, errors, summary |
| 1 | `-v` | Source, output paths, applied options |
| 2 | `-vv` | HTTP requests with timing, retries, size estimates |
| 3 | `-vvv` | Response headers, workflow trace, tracebacks |

Diagnostic lines go to stderr with tagged prefixes (`[http]`, `[retry]`,
`[scrape]`, `[timing]`, `[download]`).

Set `COMIC_DL_TRACE_HTTP=1` to surface per-request response headers at `-vv`
without needing `-vvv`.

## Interrupting a run

Press **Ctrl-C** once to stop gracefully. The current download finishes,
partial `.part` files are kept for resume, and the process exits with code
130. A resume hint echoes the command you ran.

Press **Ctrl-C** twice within 2 seconds to force-quit immediately.

SIGTERM behaves identically to SIGINT.

## Platform notes

- **Windows:** use Windows Terminal for Unicode support. In classic `cmd.exe`,
  run `chcp 65001` first.
- **Windows long paths:** deep series trees can exceed `MAX_PATH`. Enable the
  system long-path policy or keep the output directory short.
- **macOS:** no binaries are distributed yet — build from source (`git clone`
  + `uv sync`). See [Installation](../install.md).
