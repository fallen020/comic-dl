# Environment Variables

comic-dl reads the following environment variables.

## Config

| Variable | Description |
| :------- | :---------- |
| `COMIC_DL_CONFIG` | Override the config file path |

## Color control

These are checked in precedence order. Explicit `--color` flags always win.

| Variable | Effect |
| :------- | :----- |
| `NO_COLOR` | Set (even to `0`) to disable colors |
| `CLICOLOR_FORCE` | Set to force colors |
| `CLICOLOR` | `0` = never, `1` = always |
| `FORCE_COLOR` | `0` = never, `1` = 16-color, `2` = 256-color, `3` = truecolor |
| `TERM` | `dumb` or `unknown` disables colors |
| `COLORFGBG` | `fg;bg` index pair — background index ≥ 8 means light background |

## Diagnostics

| Variable | Effect |
| :------- | :----- |
| `COMIC_DL_TRACE_HTTP` | Any non-empty/non-false value enables header-level HTTP traces |
| `COMIC_DL_ASCII` | `1` forces ASCII glyphs (no Unicode) |

## Display

| Variable | Effect |
| :------- | :----- |
| `DISPLAY` | X11 display for webview solver (Linux) |
| `WAYLAND_DISPLAY` | Wayland display for webview solver (Linux) |
| `VISUAL` / `EDITOR` | Editor for `config edit` |

## Color mode

`--color` controls when ANSI colors appear:

| Value | Behavior |
| :---- | :------- |
| `auto` (default) | Colors on interactive terminal, plain when piped. Consults env vars first. |
| `always` | Force ANSI output (e.g. for `comic-dl ... \| less -R`) |
| `never` | Force plain output (alias for `--no-color`) |

`--json` output is always plain, regardless of color mode.
