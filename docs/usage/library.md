# Library Management

comic-dl tracks every download in a local SQLite database. This enables
skip/resume, subscription updates, and library queries.

## List series

```bash
comic-dl list
comic-dl list --json
comic-dl list --source e-hentai.org    # filter by source domain
```

## Show series info

```bash
comic-dl info "My Favorite Series"
comic-dl info "My Favorite Series" --json
```

The info view shows chapters with verification status: `✓` (file present on
disk), `✗` (file missing), or `–` (never downloaded).

## Recent downloads

```bash
comic-dl latest            # last 7 days
comic-dl latest -n 30      # last 30 days
comic-dl latest --json
comic-dl latest --source e-hentai.org
```

## Update tracked series

Re-scrape a series and download newly released chapters:

```bash
comic-dl update all                      # every tracked series
comic-dl update "My Favorite Series"     # one series by title, ID, or URL
```

Only new chapters are fetched. Existing chapters are skipped via the library.
The series' `last checked` and `last updated` timestamps are refreshed.

Series without a stored source URL, or without a series-page endpoint, are
reported as skipped.

Large libraries update faster in parallel — cap how many series re-scrape at
once with `--parallel` (default 1 keeps runs sequential):

```bash
comic-dl update all --parallel 4       # update up to 4 series concurrently
```

Parallel series still share the per-host rate limiter, so they never exceed
the politeness budget.

## Remove a series

```bash
comic-dl remove "My Favorite Series"
comic-dl remove "My Favorite Series" --dry-run    # preview
```

Moves the series folder to `<output>/.comic-dl/trash/` and forgets it.
Trashed series are purged after 7 days.

## Restore a series

```bash
comic-dl restore "My Favorite Series"
```

Moves a trashed series back and restores its library entry, including original
timestamps. Resolves by title, series ID, or source URL. Refuses to clobber an
existing directory or entry.

## Resolving series names

`info`, `remove`, `restore`, and `update` resolve series by title, ID, or URL.
If the target cannot be resolved (no match, or ambiguous with candidates
shown), the command exits with code `2`.

A mistyped `--source` domain gets a "Did you mean" suggestion.

## JSON output

All library commands support `--json` for scripting:

```bash
comic-dl list --json
# {"schema_version": 1, "series": [...]}

comic-dl info <series> --json
# {"schema_version": 1, ..., "chapters": [...]}

comic-dl latest --json
# {"schema_version": 1, "chapters": [...]}
```

Error messages still go to stderr. The `schema_version` field enables scripts
to detect breaking changes.

## Bad output path

A non-existent, non-directory, or non-writable `-o` path is a usage error
(exit code `2`) with a hint pointing at the default output root.
