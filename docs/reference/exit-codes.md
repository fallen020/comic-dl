# Exit Codes

comic-dl uses standard process exit codes to communicate results to scripts
and CI systems.

## Codes

| Code | Constant | Meaning |
| :--- | :------- | :------ |
| `0` | `EXIT_OK` | Success. Nothing reported as error. |
| `1` | `EXIT_ERROR` | Runtime error. A download, library, or network step failed. |
| `2` | `EXIT_USAGE` | Invalid CLI usage. Bad flag, bad value, unknown command. |
| `130` | `EXIT_INTERRUPTED` | Interrupted by Ctrl-C, SIGINT, or SIGTERM. |

Use the named constants — never bare integers — in any code that checks exit
status.

## Error kinds

Every :class:`~comic_dl.errors.ComicError` carries a stable machine-readable
`kind` alongside its message and exit code, exposed by
:func:`comic_dl.ui.error_kind`. Branch on kinds instead of parsing message
text.

| Kind | Exception | Meaning |
| :--- | :-------- | :------ |
| `usage` | `ValidationError` | Invalid input or usage (exit 2). |
| `scrape` | `ScrapeError` | Page-level scrape failure (no images, auth wall). |
| `download` | `DownloadError` | Runtime download failure. |
| `timeout` | `ScrapeTimeout`, `DownloadTimeout` | Hard timeout exceeded. |
| `library` | `LibraryError` | Library database or operation failure. |
| `network` | — (curl_cffi, builtin `ConnectionError`) | Connectivity problem. |
| `os` | — (builtin `OSError`) | Filesystem or OS-level failure. |
| `internal` | anything else | Unexpected internal error. |

## When each code applies

### Exit 0

- All URLs downloaded successfully (or skipped as already-downloaded).
- Library commands (`list`, `info`, `latest`) complete without error.
- `--dry-run` completes without error.
- `--list-sources` completes.
- `config validate` finds no problems.

### Exit 1

- One or more URLs failed to download (network error, server error, gallery
  unavailable).
- A library operation fails after the target series is resolved (disk error,
  database error).
- An estimate exceeds `--max-size`.
- `config validate` finds problems.

### Exit 2

- Unknown flag or command.
- Invalid flag value (e.g. `--concurrency 0`).
- `--chapters` contains invalid syntax.
- `info`, `remove`, `restore`, `update` cannot resolve the target series (no
  match, or ambiguous with candidates shown).
- Bad `-o` path (doesn't exist, isn't a directory, or isn't writable).

### Exit 130

- User pressed Ctrl-C once (graceful stop at next item boundary).
- User pressed Ctrl-C twice within 2 seconds (force quit).
- SIGTERM received.
- A confirmation was required but could not be answered: `--force` with a
  multi-URL run in non-interactive mode when re-downloads would occur,
  `library remove --json` without `-y`, or EOF on an interactive prompt.

## Interrupt behavior

Ctrl-C (SIGINT) and SIGTERM use a two-stage cooperative model:

1. **First press** — stops at the next item boundary (chapter or URL). The
   current download finishes, partial `.part` files are kept, and the process
   exits 130. A resume hint echoes the command you ran.
2. **Second press within 2 seconds** — force-exits immediately via
   `os._exit(130)`.

A second press after the 2-second grace window resets the timer.

## Batch processing

When downloading from a file (`-f`), each URL is processed independently. If
some fail and some succeed, the exit code is `1`. A summary is printed at the
end showing succeeded, skipped, and failed counts.
