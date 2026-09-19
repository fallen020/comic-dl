# CLI Reference

comic-dl's command-line interface. The default command downloads content when
given a URL.

## Synopsis

```
comic-dl [OPTIONS]
comic-dl -u <URL> [OPTIONS]
comic-dl <URL> [OPTIONS]
comic-dl -f <FILE> [OPTIONS]
comic-dl <COMMAND> [ARGS]
```

## Commands

### Download

| Command | Description |
| :------ | :---------- |
| `comic-dl -u <URL>` | Download a single gallery/series URL |
| `comic-dl <URL>` | Same as `-u <URL>` (positional shorthand) |
| `comic-dl -f <FILE>` | Download URLs from a text file (one per line) |
| `comic-dl` (no args) | Interactive prompt (requires a TTY) |

### Library

| Command | Description |
| :------ | :---------- |
| `comic-dl list [--json] [--source <DOMAIN>]` | List series in the library |
| `comic-dl info <SERIES> [--json]` | Show details for one series |
| `comic-dl latest [-n N] [--json] [--source <DOMAIN>]` | Chapters downloaded in the last N days (default 7) |
| `comic-dl update <SERIES\|all>` | Re-scrape tracked series, download new chapters |
| `comic-dl remove <SERIES> [--dry-run]` | Move a series to trash |
| `comic-dl restore <SERIES>` | Bring a trashed series back |

### Configuration

| Command | Description |
| :------ | :---------- |
| `comic-dl config path` | Print the effective config file path |
| `comic-dl config show` | Print resolved configuration (defaults + file) |
| `comic-dl config list` | Print as TOML to stdout |
| `comic-dl config validate` | Type-check the config file |
| `comic-dl config init [--force]` | Write a documented default config |
| `comic-dl config edit` | Open config in `$VISUAL` / `$EDITOR` |

### Cache & Cookies

| Command | Description |
| :------ | :---------- |
| `comic-dl cache status` | Show cache location, TTL, size budget, entry count |
| `comic-dl cache clear [-y]` | Delete all cached entries |
| `comic-dl cookie ls [HOST] [--json]` | List stored cookies |
| `comic-dl cookie set <HOST> <NAME> <VALUE>` | Store a cookie |
| `comic-dl cookie clear [HOST] [-y]` | Clear cookies |

### Self

| Command | Description |
| :------ | :---------- |
| `comic-dl self version` | Print the installed version |
| `comic-dl self update [--check] [-y] [--channel beta]` | Check for and install updates through the tool that installed comic-dl |
| `comic-dl self site list [--json]` | List installed site adapters, their versions, and cached status |
| `comic-dl self site check [SITE] [--live] [--json]` | Compare installed adapter versions against the latest release manifest; `--live` runs a live check for one site |
| `comic-dl self site update SITE` / `--all` | Update site support (bundled adapters update with the core) |

`self update` refuses to guess: package-manager installs are updated by the
owning package manager, pip/uv installs by the tool that created them, and a
source checkout or unknown installation is reported with manual
instructions. See [Self-Management](../usage/self-update.md) and
[Site Support](../usage/site-support.md) for the per-installation behaviour.

### Other

| Command | Description |
| :------ | :---------- |
| `comic-dl --list-sources [--json] [--plugin] [QUERY]` | List/search supported sites |
| `comic-dl plugin list [--json]` | List installed third-party sources (including broken ones) |
| `comic-dl plugin validate <PATH>` | Shape-check a plugin's `Source` class offline |
| `comic-dl plugin scaffold <NAME> [--domain HOST]` | Generate a plugin package skeleton |
| `comic-dl completion bash\|zsh\|fish` | Print shell completion script |
| `comic-dl help [COMMAND]` | Show help |

## Global flags

### Input/Output

| Flag | Short | Default | Description |
| :--- | :---- | :------ | :---------- |
| `--url` | `-u` | — | Single URL to download |
| `--file` | `-f` | — | Text file with one URL per line |
| `--output` | `-o` | `~/Downloads/comic-dl` | Output directory |
| `--chapters` | | | Chapter subset by chapter number. Examples: `1-3,7` (1,2,3,7), `1-3,7,10-` (1,2,3,7,10+), `all` (all chapters), `0` (prologue/promo). Omit for interactive checkbox picker. |

### Performance

| Flag | Default | Description |
| :--- | :------ | :---------- |
| `--concurrency` / `-c` | `5` | Page images downloaded in parallel (1–32) |
| `--parallel` | `5` | Max URLs in flight across a batch (1–16) |
| `--chapter-parallel` | `1` | Max chapters of a series at once (1–8) |

### Update

| Flag | Default | Description |
| :--- | :------ | :---------- |
| `--parallel` / `-p` | `1` | Max series updating at once (1–16; default keeps runs sequential) |

Series in parallel still pass through the per-host rate limiter, so raising
`update --parallel` never exceeds the politeness budget.

### Safety & limits

| Flag | Default | Description |
| :--- | :------ | :---------- |
| `--force` | | Overwrite existing archives |
| `--no-clobber` | | Never overwrite existing archives (default; conflicts with `--force`) |
| `--max-image-size` | `100 MB` | Maximum size per image |
| `--max-size` | `0` (unlimited) | Maximum total download size per run |
| `--impersonate` | `chrome146` | TLS/HTTP impersonation profile |
| `--solver` | `auto` | Cloudflare challenge solver. Modes: `auto` (impersonation first, webview if needed), `impersonation` (TLS/HTTP fingerprint only; fast, no deps), `webview` (system WebView/GTK; needs display + GTK libs), `off` (disable solver; challenged sites fail) |

### Behavior toggles

| Flag | Default | Description |
| :--- | :------ | :---------- |
| `--no-cookie` | | Disable persistent cookie jar for this run |
| `--no-cache` | | Disable scrape response cache for this run |
| `--no-rate` | | Disable per-site rate limiting for this run |
| `--no-generic` | | Disable generic fallback scraper for this run |
| `--dry-run` | | Preview what would download without writing |
| `--no-banner` | | Suppress the startup banner |

### Output control

| Flag | Default | Description |
| :--- | :------ | :---------- |
| `--json` | | Machine-readable JSON on stdout |
| `--quiet` / `-q` | | Errors only (scripts/cron) |
| `--verbose` / `-v` | | Increase verbosity (up to `-vvv`) |
| `--no-color` | | Disable ANSI colors |
| `--color` | `auto` | `auto`, `always`, or `never` |
| `--debug-file` | | Redirect `-vvv` trace to a file |

### Archive

| Flag | Default | Description |
| :--- | :------ | :---------- |
| `--format` | `cbz` | Archive format: `cbz`, `zip`, `cbt` |
| `--compress` | `stored` | Compression: `stored`, `deflate`, `deflate:0-9` |

### Config

| Flag | Description |
| :--- | :---------- |
| `--config` | Path to a custom `config.toml` |
| `--no-config` | Ignore `config.toml` for this run |
| `--list-sources` | List registered sources and exit |
| `--help` / `-h` / `-?` | Show help |

`--quiet` and `--verbose` are mutually exclusive.

## JSON output

Every `--json` payload includes a `schema_version` field (currently `1`).

| Command | Output shape |
| :------ | :----------- |
| `comic-dl list --json` | `{"schema_version": 1, "series": [...]}` |
| `comic-dl latest --json` | `{"schema_version": 1, "chapters": [...]}` |
| `comic-dl info <S> --json` | `{"schema_version": 1, ..., "chapters": [...]}` |
| `comic-dl --list-sources --json` | `{"schema_version": 1, "sources": [...]}` |
| `comic-dl -u <URL> --json` | `{"schema_version": 1, "url": ..., "status": "success"\|"failed"\|"skipped", ...}` |
| `comic-dl -f <FILE> --json` | `{"schema_version": 1, "urls": [...], "succeeded": N, "skipped": N, "failed": N}` |
| `comic-dl update <S> --json` | `{"schema_version": 1, "checked": N, "changed": N, "skipped": N, "failed": [...], "series": [...]}` |

`--json` implies non-interactive mode and uses the same exit codes as the
human-facing run.

## Exit codes

| Code | Meaning |
| :--- | :------ |
| `0` | Success |
| `1` | Runtime error (download failed, network error) |
| `2` | Invalid CLI usage (bad flag, bad value) |
| `130` | Interrupted (Ctrl-C / SIGTERM) |

See [Exit Codes](exit-codes.md) for details.
