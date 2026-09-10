# Troubleshooting

A download failed. Work the error below in order — each one says what
comic-dl already tried, what to do first, what success looks like, and
when to stop.

If no section matches, capture a [debug log](#capturing-a-debug-log) and
attach it to an issue.

## Download errors

### Access blocked (403)

comic-dl already tried to get through: it detects Cloudflare challenges,
solves them by replaying with a fresh fingerprint and then with the system
webview when one is available, and retries once. A 403 that still reaches
you means that ladder failed. Work through these in order:

1. **Open the URL in a normal browser.**
   - The browser is blocked too — the site is stopping your IP or region,
     or demands a login the scrape cannot pass. Stop here: no comic-dl
     setting changes a login wall or an IP block.
   - The browser loads the page — go to step 2.
2. **Change the impersonation profile.** The default is `chrome146`, the
   newest build, and some sites reject the newest signatures outright.
   Try an older Chrome or another engine:

   ```bash
   comic-dl -u <URL> --impersonate firefox133 -vvv
   ```

   Still 403 — step 3.
3. **Force the webview solver**: `--solver webview`. On headless Linux,
   install WebKitGTK and PyGObject first; see [No webview
   available](#no-webview-available) below. With `-vvv`, the log shows
   `cf: escalating to system webview`, then `webview harvested fresh
   cf_clearance`, then a successful retry.
4. **Still 403?** Some hosts bind the clearance cookie to the webview's
   own TLS fingerprint (kagane.to), so plain HTTP rejects the fresh
   cookie when it replays it. The `-vvv` log shows exactly this path, and
   no setting changes it — attach the log to an issue.

Stop when step 1 shows a login or IP wall, or step 4 shows a
fingerprint-bound cookie. Both are site behavior, not a comic-dl bug.

### Not found (404)

comic-dl asked for one specific address and the site answered that it does
not exist. The address came from the URL you typed or from a series
listing:

- **You typed or pasted the URL** — compare it with the browser address
  bar. The e-hentai `gid/token`, the WEBTOON `title_no`/`episode_no`, and
  the MangaDex UUID must match exactly.
- **A series that used to work** — the site moved, renamed, or deleted the
  gallery. Search the site for the current address and use it.
- **One chapter inside a series** — that chapter was deleted; the others
  still download. Rerun and select the chapter subset without the dead
  one: `--chapters 1-3,5`.

### Rate limited (429)

comic-dl already waited: 429 is retried with backoff, honoring the
server's `Retry-After` header for up to 30 seconds per pause. Reaching
this error means the retries still failed because the host wants fewer
requests than you sent.

- **A one-off burst** — rerun the same command about a minute later. The
  per-hour windows most sites enforce clear on their own.
- **A site you hit often** — slow down for good: lower `--concurrency`
  from its default of 5, and add a per-host rate in the config, e.g.
  `rate = { "e-hentai.org" = 0.5 }` for one request every two seconds.
- **e-hentai specifically** — the image nodes throttle large bursts with a
  509 bandwidth-limit page instead of a 429. comic-dl already treats that
  page as transient and backs off, so keep the default rate and rerun once
  the burst window passes. There is nothing to tune.

### Unavailable (451)

The host is refusing the content based on where you connect from — most
sites use 451 to comply with local legal blocks. No URL, cookie, or
comic-dl setting changes the host's answer; find the series on another
site. Reaching it from a region the site does serve is a network change on
your side, not a comic-dl fix.

### No images found on this page

The chapter page loaded but contained no images: the page may require
login, be region-locked, or have been removed. Site-specific causes:

- **e-hentai** — the gallery is missing or expunged, or is behind a login.
  For a login-gated gallery, run once with `--solver webview`, complete
  the login in the window that opens, and comic-dl keeps the session
  cookies for the next run.
- **Pawchive** — a "Previews only" post downloads thumbnail resolution
  with a warning, and a post with no visible file attachments yields no
  images at all.
- **MangaDex** — the at-home server reporting no pages is a transient
  server state; rerun once after a minute.
- **Asura Scans** — pages marked `Premium` are locked and raise this
  error; nothing to fix.
- **WEBTOON** — episodes behind authentication or a paywall raise this
  error.

### e-hentai API error

The gallery API answered with an error text instead of gallery data:

- **`Key missing`** — the gallery is gone, expunged, or the token no
  longer matches. Verify the URL in a browser.
- **Anything else** — a server-side problem; rerun once after a minute.
- **The throttle/ban page** (`IP address has been temporarily banned`) is
  served on normal page fetches, not this API call. comic-dl recognizes
  it as transient and retries; it is the site enforcing its own rate and
  clears when the fetching stops.

### Unsupported URL

No scraper handled the URL. On unknown hosts comic-dl tries the generic
fallback unless you passed `--no-generic` or disabled it in the config;
this error means the fallback is off or found nothing. Check the
[supported sites](reference/supported-sites.md) list, or install a plugin
that handles the host.

### ModuleNotFoundError

The source tree is present but the environment is not set up. Run
`uv sync` once.

### Permission denied

The output directory is not writable, or is not a directory. Point the run
at a writable location with `--output`, or fix the directory permissions.

### No webview available

The webview solver needs a platform backend to start:

- **Windows and macOS** — the system webview is built in; nothing to
  install.
- **Linux** — install PyGObject and WebKitGTK from your distro's package
  manager and run on a display.
- **Headless Linux** — wrap the run in `xvfb-run`, or use `--solver
  impersonation`, which never opens a window.

## How retries work

A failing request is retried before you ever see the error:

- **Metadata fetches** — retried with backoff, honoring the server's
  `Retry-After` header up to 30 seconds per pause.
- **Image downloads** — transient statuses (408, 429, 500, 502, 503, 504,
  509, 530) and timeouts are retried up to 3 attempts with 2s/4s backoff
  and jitter. A response that carries HTML where image bytes belong, such
  as an e-hentai throttle page, counts as transient too.
- **403 challenges** — solved once and retried once, as described under
  [Access blocked](#access-blocked-403).

No request is retried forever, and the per-host rate limiter still paces
the run between attempts.

## Batch failures

With a URL file or a multi-series run, each URL is independent: one
failure does not stop the rest. The run closes with a summary of
succeeded, skipped, and failed counts, and the exit code is 1 when any
URL failed.

## Interrupted downloads

Ctrl-C stops the run: the current download finishes, partially written
`.part` files are kept, and a resume hint is printed. Re-run the same
command and each file resumes at the byte offset recorded in its `.part`
file. comic-dl does not re-check the remote for changes, so if a site
replaced an image mid-run the resumed copy could mix old and new bytes.
Delete the affected `.part` file and re-run if a page ever looks corrupted
after a resume.

## Capturing a debug log

When an error above does not explain the failure, re-run with diagnostics
and attach the log when you report it:

```bash
comic-dl -u <URL> -vvv --debug-file debug.log
```

`-v` narrates the run, `-vv` adds network lines (HTTP, retries, timings),
and `-vvv` adds pipeline internals. All diagnostics go to stderr, so a
`--json` run keeps a clean payload on stdout.

The log never contains cookies, tokens, or authorization headers:
credentials are masked and the debug file is written owner-only, so the
file is safe to paste into a report.

## Platform issues

### Windows console glyphs

The CLI prints Unicode glyphs. Use Windows Terminal, or run `chcp 65001`
in classic `cmd.exe`, when the output shows broken glyphs.

### Windows long paths

Deep series trees can exceed `MAX_PATH`. Enable the system long-path
policy or keep the output directory short.

### macOS

macOS binaries are not published yet — build from source with `git clone`
plus `uv sync`; see [Installation](install.md). A standalone binary needs
code signing and notarization first.

## Getting help

Start from a debug log using the [command above](#capturing-a-debug-log) —
reports without one cannot be diagnosed.

- **Questions** — [Discussions](https://github.com/fallen020/comic-dl/discussions)
- **Bugs** — [Issues](https://github.com/fallen020/comic-dl/issues/new?template=bug_report.yml)
- **Security** — [Security advisory](https://github.com/fallen020/comic-dl/security/advisories/new)
