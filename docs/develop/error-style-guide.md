# Error Style Guide

How comic-dl talks to its user when things go wrong. The goal is one sentence:
a user should always know **what** went wrong, **why**, and **what to do
next** — without ever seeing a Python traceback or an internal module path.
Read this before you write any new failure path, and paste it into review
comments when someone ships a bare `print(traceback.format_exc())`.

## The three hard rules

1. **Start lowercase, end with a period.** Messages read mid-sentence.
2. **Lead with the object/context, then a colon, then the problem.**
   You name what failed before you say why.
3. **Every error carries an actionable hint where one exists.** If there is a
   fix ("point `-o` at the right root", "lower the concurrency"), the message
   says it.

And the two "never"s:

- **Never show implementation details** in level-0 output: tracebacks,
  exception class names, module paths, internal state.
- **Never drop user-actionable details**: "Permission denied", the offending
  value, the host, the invalid switch.

## Canonical examples

```text
invalid size: '10x' (e.g. 100MB, 2GB, 512KB, 104857600)
Invalid chapter selection '5-2': reversed range 5-2.
File not found: /nonexistent/urls.txt
Could not create output directory: /data/dl (Permission denied)
  Download a series first, or point -o at the right output root.
```

Read each as *object*, colon, *problem*, then *fix*. The last example shows
the two-line form: a `print_error_detail(context, reason, hint)`.

## Streams: what goes where

| Stream | Contains |
| :----- | :------- |
| **stdout** | Final results: "Saved" lines, summaries, tables, JSON, `--help`, `self version` |
| **stderr** | Everything else: banners, progress, warnings, errors, retries, prompts |

Scripts pipe stdout; progress noise lives on stderr so piping stays clean.
Keep it strict — a stray `print()` to stdout in a flow that ends in JSON
(like `--json` machine output) breaks consumers.

## Building blocks

Every message goes through a `ui.py` helper. Do not compose Rich `Console`
calls ad hoc for user-facing text.

| Helper | Stream | Use for |
| :----- | :----- | :------ |
| `print_error(message)` | stderr | One-line failure |
| `print_warning(message)` | stderr | Non-fatal caution |
| `print_error_detail(context, reason, hint)` | stderr | Failure with reason and hint |
| `report_error(exc, context, hint)` | stderr | Map exception to message + exit code |
| `print_success(message)` | stdout | Successful result |
| `print_dim(message)` | stderr | Muted context |

`report_error` is the one door that sees a raw exception. It maps the
exception to a user message **and** an exit code; it prints the traceback
only at `-vvv`.

## Exit codes

| Code | Constant | Use when |
| :--- | :------- | :------- |
| 0 | `EXIT_OK` | Success |
| 1 | `EXIT_ERROR` | Download/library/network failure |
| 2 | `EXIT_USAGE` | Bad flag, bad value, unknown command |
| 130 | `EXIT_INTERRUPTED` | Ctrl-C / SIGTERM |

Use the named constants from `errors.py` — never bare integers. Scripts and
shell pipelines depend on the 0/1/2/130 contract; do not invent new codes
without updating the architecture doc's table.

## Mapping errors to exit codes

`errors.py` defines the exception taxonomy; each type carries its default
exit code. New exception types subclass the existing ones — do not raise a
bare `ComicError` with a code stashed in a message string, or the `report_error`
path cannot map it. The mapping rule: **usage errors → 2, every operational
failure → 1.** A wrong flag is a bug in the invocation and must not look like
a download failure.

Two machine-readable hooks ride on top of the human message and never appear
in it: `kind` (a stable category: `usage`, `download`, `scrape`, `timeout`,
`library`, ...) lets JSON mode and the library branch without parsing prose,
and `ScrapeError.site_error_code` carries the `SITE_*` constant for
site-support checks.

## Verbosity and diagnostics

| Level | Flag | Shows |
| :---- | :--- | :---- |
| 0 | (default) | Progress, status, results, warnings, errors, summary |
| 1 | `-v` | Source, output paths, options, metadata |
| 2 | `-vv` | HTTP requests, retries, timing, size estimates |
| 3 | `-vvv` | Response headers, workflow trace, tracebacks |

Rules:

- Level-0 UI is only the `print_*` helpers, never `vlog()`.
- `vlog(level, message, tag=...)` writes to stderr only at the right level.
  Tags are a fixed vocabulary: `[http]`, `[retry]`, `[scrape]`,
  `[timing]`, `[download]`.
- Tracebacks print only at `-vvv`, via `report_error`.

Expect a bug report attached with `-vvv` output (see
[reporting.md](reporting.md)); the diagnostic levels exist to make that
debuggable without exposing internals at level 0.

## Glyphs and ASCII fallback

All status symbols come from the single `ui.glyphs()` set. When the terminal
cannot emit UTF-8 — or `COMIC_DL_ASCII=1` is set — the UI falls back to
ASCII, and the non-TTY spinner is always ASCII (piped output must not mojibake).

Never hardcode `✓`/`✗`/spinner frames in a string; route them through
`glyphs()`. Tests assert on content with `output.replace("\n", "")` precisely
because glyph runs are width- and encoding-dependent (see
[testing.md](testing.md)).

## Live rendering: the two jitter rules

Live areas (the Activity batch table, the Pipeline spinner) must never
jitter between frames:

1. **Fixed-width numeric slots** — every ticking readout (bytes, speed, ETA)
   is right-aligned to a constant column width.
2. **One frame owner per `Live`** — each `Live` has exactly one render loop.
   Row/state events mark the renderer dirty; the loop rebuilds at most once
   per tick.

A jittering frame (column width jumping between renders) looks like a bug and
reads like a crash under a progress bar. When you change a live renderer, re-read
these two rules and re-test with a slow terminal.
