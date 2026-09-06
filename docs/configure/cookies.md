# Cookies & Anti-Bot Handling

## Persistent cookie jar

comic-dl persists an RFC-compliant cookie jar across runs so session cookies
(such as Cloudflare's `cf_clearance`) survive for hours.

| Platform | Location |
| :------- | :------- |
| Linux | `~/.config/comic-dl/cookies.db` |
| macOS | `~/Library/Application Support/comic-dl/cookies.db` |
| Windows | `%APPDATA%\comic-dl\cookies.db` |

The jar is stored in SQLite (WAL mode). Session cookies (no expiry) are kept
in-memory for the current process only and never persisted.

## Managing cookies

```bash
comic-dl cookie ls                     # list all stored cookies
comic-dl cookie ls kagane.to           # list cookies for a host
comic-dl cookie ls --json              # machine-readable output
comic-dl cookie set <host> <name> <value> [--expires <epoch>]
comic-dl cookie clear                  # clear all cookies
comic-dl cookie clear kagane.to        # clear cookies for a host
```

!!! warning
    `cookie set` passes the value on the command line, making it visible in
    shell history and to other users via `ps`. Prefer letting the tool capture
    cookies itself (via the webview solver), or clear the value afterwards.

## Disabling cookies

```bash
comic-dl -u <URL> --no-cookie          # stateless for one run
```

Or in config:

```toml
[http]
cookie-jar = false
```

When the cookie jar is disabled, the scrape response cache is also bypassed.

## Cloudflare challenge handling

When a request returns a Cloudflare challenge (403/503 interstitial), comic-dl
clears the stale cookie and retries once. The retry can be solved in the
system webview.

### Solver modes

| Mode | Flag | Behavior |
| :--- | :--- | :------- |
| `auto` | `--solver auto` (default) | Detect challenge → clear stale cookie → retry. Opens webview if available, falls back to impersonation. |
| `impersonation` | `--solver impersonation` | Never open webview. Rely on TLS/HTTP fingerprint profile. |
| `webview` | `--solver webview` | Force system webview. Falls back to impersonation if it cannot start. |
| `off` | `--solver off` | Never retry challenged requests. |

### Webview requirements

The webview solver is bundled with the package. Your platform needs a working
backend:

| Platform | Backend | Notes |
| :------- | :------ | :---- |
| Windows | Edge WebView2 | Built-in on Windows 10+ |
| macOS | WKWebView | Built-in |
| Linux | WebKitGTK | Requires PyGObject + WebKitGTK from distro packages |

On headless Linux, the tool falls back to `xvfb-run` when available, then to
impersonation-only.

A visible window is shown because Cloudflare fingerprints hidden/offscreen
renders. A stored `cf_clearance` is reused for its full validity (~1–2 hours).

### TLS fingerprint binding

Some sites (e.g. kagane.to) bind `cf_clearance` to the exact TLS fingerprint.
The webview session serves requests from inside the page so the fingerprint
matches. Plain `curl_cffi` replay fails with 403 on these sites.

## Impersonation profiles

TLS/HTTP fingerprint profiles simulate a real browser:

```bash
comic-dl -u <URL> --impersonate chrome131
```

Default: `chrome146`. Configurable via `[http] impersonate` or `--impersonate`.

Deprecated profiles (chrome99–104, edge99, edge101, safari15_3/15_5) are
allowed with a warning.

## Rate limiting

Per-site throttling spaces out requests. Defaults exist for:

| Host | Rate |
| :--- | :--- |
| `kagane.to` | 1.5 req/s |
| `kstatic.to` | 2.0 req/s |
| `e-hentai.org` | 2.0 req/s |

Override or add hosts under `[http] rate`. Per-host overrides in
`[sources."<host>"]` take highest precedence.

Disable entirely with `--no-rate` or `[http] rate-enabled = false`.

!!! note "e-hentai"
    The `/s/` image-page fetches run at 2 req/s matching the default. If you
    see "image limit reached" errors, lower `e-hentai.org` toward `1.0`.
    Throttled responses (HTTP 509) are detected and retried automatically.
