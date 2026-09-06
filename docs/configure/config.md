# Configuration File

comic-dl can persist options in a TOML config file instead of passing flags
every run.

## Config file location

| Platform | Path |
| :------- | :--- |
| Linux | `~/.config/comic-dl/config.toml` |
| macOS | `~/Library/Application Support/comic-dl/config.toml` |
| Windows | `%APPDATA%\comic-dl\config.toml` |

## Precedence

Options are resolved in this order (later wins):

1. CLI flag
2. Config file
3. Built-in default

## Config format

```toml
# Default download directory
output = "~/Downloads/comic-dl"

# Parallel downloads
concurrency = 5              # page images per chapter (1-32)
parallel = 5                 # URLs in flight across a batch (1-16)
chapter_parallel = 1         # chapters of a series at once (1-8)
# Note: `update` uses its own `--parallel` flag (default 1) and ignores
# this `parallel` value.

# Size limits
max_image_size = "100MB"     # per image
max_size = 0                 # per run (0 = unlimited)

[http]
impersonate = "chrome146"    # TLS/HTTP fingerprint profile
solver = "auto"              # auto | impersonation | webview | off
cookie-jar = true            # persist session cookies across runs
cache = true                 # on-disk scrape response cache
cache-ttl = 6                # hours a cached response stays fresh
cache-max-bytes = "50MB"     # total cache size budget (100MB, 2GB, 512KB, or bytes);
                             #   when exceeded, the oldest entries are evicted
cache-max-entries = 5000     # advisory entry cap; eviction is governed by
                             #   cache-max-bytes + the 14-day hard drop age
rate-enabled = true          # per-site request throttling
download-timeout = 60        # seconds before one image fetch is abandoned
download-retries = 2         # retries per image after the first attempt

# Per-host rate limits (requests/second)
rate = { "kagane.to" = 1.5, "kstatic.to" = 2.0, "e-hentai.org" = 2.0 }

[archive]
format = "cbz"               # cbz | zip | cbt
compression = "stored"       # stored | deflate | deflate:0-9

[download]
generic = true               # generic fallback scraper for unknown hosts
tmp-dir = ""                 # chapter staging dir; "" = system temp (set to a
                             #   large stable disk if /tmp is small or tmpfs)

# Per-source overrides (host keys must be quoted)
# [sources."kagane.to"]
# rate = 0.8
```

See [`examples/config.toml`](https://github.com/fallen020/comic-dl/blob/main/examples/config.toml)
for a fully documented example.

## Config management commands

| Command | Description |
| :------ | :---------- |
| `comic-dl config path` | Print the effective config file path |
| `comic-dl config show` | Print the resolved configuration (defaults + file) |
| `comic-dl config list` | Print as TOML to stdout (for scripting) |
| `comic-dl config validate` | Type-check the config file |
| `comic-dl config init` | Write a documented default config |
| `comic-dl config edit` | Open the file in `$VISUAL` / `$EDITOR` |

`config init` refuses to overwrite an existing file unless `--force` is passed.

## Per-source overrides

Host-specific settings live in `[sources."<host>"]` tables. The host key
**must be quoted** — unquoted `[sources.kagane.to]` would nest incorrectly.

The supported per-host key is `rate`, which overrides both `[http] rate` and
the built-in default for that host.

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

A missing or malformed config file is ignored — the tool always runs with
defaults. A malformed file prints a one-time warning. `--no-config` skips the
file entirely for a single run, which is the escape hatch for a broken config.

At `-v` or above, a run prints the effective config file it loaded.
