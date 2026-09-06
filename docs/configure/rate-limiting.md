# Rate Limiting

comic-dl spaces out requests to each host using a per-host token bucket
limiter. This prevents aggressive fetching from getting your IP throttled or
banned.

## Default rates

| Host | Rate |
| :--- | :--- |
| `kagane.to` | 1.5 req/s |
| `kstatic.to` | 2.0 req/s |
| `e-hentai.org` | 2.0 req/s |

Fractional rates are honored precisely: `1.5` means one request every ~0.67s.

## Configuring rates

### Global defaults

```toml
[http]
rate-enabled = true
rate = { "kagane.to" = 1.5, "kstatic.to" = 2.0, "e-hentai.org" = 2.0 }
```

### Per-host overrides

Host-specific rates in `[sources."<host>"]` override `[http] rate`:

```toml
[sources."kagane.to"]
rate = 0.8
```

The precedence is: `[sources."<host>"].rate` > `[http] rate` > built-in default.

## Disabling rate limiting

```bash
comic-dl -u <URL> --no-rate
```

Or in config:

```toml
[http]
rate-enabled = false
```

!!! warning
    When rate limiting is disabled, page concurrency is clamped to 5 (the
    recommended politeness ceiling) and a warning is emitted. This prevents
    accidental thundering-herd behavior.

## How it works

Every outbound fetch (scrape, image, cover) calls `await_ratelimit(host)`
before the request. This is enforced for both built-in and plugin sources.

When any download hits a retryable error, all in-flight downloads pause for up
to 2 seconds (shared cooldown). This prevents ramping up under throttling
instead of backing off.

## Concurrency interaction

With rate limiting **on**, your `--concurrency` value is honored (capped at
32). With rate limiting **off**, page concurrency is clamped to 5 regardless
of your setting.
