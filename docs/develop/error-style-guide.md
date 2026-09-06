# Error Style Guide

Conventions for the messages users see when comic-dl fails. A user should
always know **what** went wrong, **why**, and **what to do next**.

## Hard rules

- **Start lowercase, end with a period.** Messages read mid-sentence.
- **Sentence case** — not Title Case.
- **Lead with the object/context**, then a colon, then the problem.
- **Every error carries an actionable hint** where one exists.
- **Hide implementation details** — tracebacks, Python exception class names,
  module paths, and internal state must not appear in level-0 output.
- **Keep user-actionable details** — "Permission denied", the offending value,
  the host, the invalid switch.

## Streams

| Stream | Contains |
| :----- | :------- |
| **stdout** | Final results: "Saved" lines, summaries, tables, JSON, `--help`, `--version` |
| **stderr** | Everything else: banners, progress, warnings, errors, retries, prompts |

This strict split lets scripts pipe stdout without progress noise.

## Building blocks

| Helper | Stream | Use for |
| :----- | :----- | :------ |
| `print_error(message)` | stderr | One-line failure |
| `print_warning(message)` | stderr | Non-fatal caution |
| `print_error_detail(context, reason, hint)` | stderr | Failure with reason and hint |
| `report_error(exc, context, hint)` | stderr | Map exception to message + exit code |
| `print_success(message)` | stdout | Successful result |
| `print_dim(message)` | stderr | Muted context |

## Canonical examples

```
invalid size: '10x' (e.g. 100MB, 2GB, 512KB, 104857600)
Invalid chapter selection '5-2': reversed range 5-2.
File not found: /nonexistent/urls.txt
Could not create output directory: /data/dl (Permission denied)
  Download a series first, or point -o at the right output root.
```

Each is: *what/where*, then colon, then *why*, with a fix when helpful.

## Exit codes

| Code | Constant | Use when |
| :--- | :------- | :------- |
| 0 | `EXIT_OK` | Success |
| 1 | `EXIT_ERROR` | Download/library/network failure |
| 2 | `EXIT_USAGE` | Bad flag, bad value, unknown command |
| 130 | `EXIT_INTERRUPTED` | Ctrl-C / SIGTERM |

Use named constants (`EXIT_OK`, `EXIT_ERROR`, `EXIT_USAGE`,
`EXIT_INTERRUPTED`) — never bare integers.

## Verbosity and diagnostics

| Level | Flag | Shows |
| :---- | :--- | :---- |
| 0 | (default) | Progress, status, results, warnings, errors, summary |
| 1 | `-v` | Source, output paths, options, metadata |
| 2 | `-vv` | HTTP requests, retries, timing, size estimates |
| 3 | `-vvv` | Response headers, workflow trace, tracebacks |

Rules:

- Level-0 UI never goes through `vlog()`. The `print_*` helpers are the
  user-facing API.
- `vlog(level, message, tag=...)` writes to stderr only when verbosity is high
  enough. Tags are a fixed vocabulary: `[http]`, `[retry]`, `[scrape]`,
  `[timing]`, `[download]`.
- Tracebacks print only at `-vvv` via `report_error`.

## Glyphs and ASCII fallback

All status symbols come from the single `ui.glyphs()` glyph set. When the
terminal can't emit UTF-8 — or `COMIC_DL_ASCII=1` is set — the UI falls back
to ASCII. The non-TTY spinner is always ASCII to prevent mojibake in piped
output.

Never hardcode a glyph in a string; route it through `glyphs()`.

## Live rendering

Live areas (Activity batch table, Pipeline spinner) must never jitter between
frames. Two rules:

1. **Fixed-width numeric slots** — every ticking readout (bytes, speed, ETA)
   is right-aligned to a constant column width.
2. **One frame owner per Live** — each `Live` has exactly one render loop.
   Row/state events mark the renderer dirty; the loop rebuilds at most once
   per tick.
