# Configuration File

comic-dl can persist options in a TOML config file instead of passing flags
every run.

## Config file location

| Platform | Path |
| :------- | :--- |
| Linux | `~/.config/comic-dl/config.toml` |
| macOS | `~/Library/Application Support/comic-dl/config.toml` |
| Windows | `%LOCALAPPDATA%\comic-dl\config.toml` |

## Precedence

Options are resolved by priority (highest first):

1. CLI flag
2. Config file
3. Built-in default

## Config format

`comic-dl config init` writes a commented starter file (the current one is
[`examples/config.toml`](https://github.com/fallen020/comic-dl/blob/main/examples/config.toml)).
Every setting is optional: uncomment a key to override its built-in
default. `comic-dl config show` prints the resolved result; `comic-dl
config validate` additionally lists the keys your file sets differently
from the defaults, which is how a stale value (a `solver` inherited from an
older template, say) shows up.

### Reference

- `solver` — `off` fails challenged sites with a hint; `auto` retries once
  (system webview when available, else impersonation); `impersonation`
  never opens a webview; `webview` forces it.
- `cookie-encryption` — `auto` (OS keyring, else `$COMIC_DL_COOKIE_KEY`),
  `keyring`, or `off` (plaintext, for CI/throwaway runs).
- `cache-max-bytes` bounds the cache; `cache-max-entries` is advisory.
  Entries older than 14 days are dropped regardless.
- `pass2-*` — a second low-rate sweep for pages pass 1 could not get, so a
  transient site-wide blip costs a slow sweep instead of a rerun.
- `compression` — `stored` (default, fastest); `deflate[:0-9]` rarely
  shrinks already-compressed pages. Zip-family only; `cbt` is never
  compressed.
- `tmp-dir` — chapter staging; defaults to the system temp dir. Point it at
  a large stable disk when `/tmp` is small or tmpfs.

## Config management commands

| Command | Description |
| :------ | :---------- |
| `comic-dl config path` | Print the effective config file path |
| `comic-dl config show` | Print the resolved configuration (defaults + file) |
| `comic-dl config validate` | Type-check the file; report keys differing from defaults |
| `comic-dl config init` | Write a commented starter config |
| `comic-dl config edit` | Open the file in `$VISUAL` / `$EDITOR` |
| `comic-dl config help` | Show help for the config commands |

`config init` refuses to overwrite an existing file unless `--force` is passed.
`config validate` lists every key your file sets differently from the
built-in defaults, which surfaces values inherited from an older template.

## Per-source overrides

Host-specific settings live in `[sources."<host>"]` tables. The host key
must be quoted; unquoted `[sources.kagane.to]` would nest incorrectly.

The supported per-host keys are:
- `rate` — requests/second, overrides both `[http] rate` and the built-in
  default for that host.
- `mode` — Cloudflare solver mode (`auto` | `impersonation` | `webview` | `off`),
  overrides `[http] solver`.
- `impersonate` — TLS/HTTP fingerprint profile, overrides `[http] impersonate`.

## Custom config path

Use a per-project config instead of the platform location:

```bash
comic-dl --config ./my-config.toml -u <URL>
```

Or set the environment variable:

```bash
export COMIC_DL_CONFIG=/path/to/config.toml
```

Precedence: `--config` > `$COMIC_DL_CONFIG` > platform location.

`--config` and `--no-config` are mutually exclusive.

## Error handling

A missing or malformed config file is ignored, so the tool always runs with
defaults. A malformed file prints a one-time warning. `--no-config` skips the
file entirely for a single run, which is the escape hatch for a broken config.

At `-v` or above, a run prints the effective config file it loaded.
