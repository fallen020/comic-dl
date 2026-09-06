"""Per-site request throttling to stay under each site's limits."""

from __future__ import annotations

import asyncio
import time

from .config import http_setting, load_config

_DEFAULT_RATES: dict[str, float] = {
    "kagane.to": 1.5,
    "kstatic.to": 2.0,
    "e-hentai.org": 2.0,
}

# Built-in cap so a misconfigured rate can never stall the batch forever.
_MAX_STALL = 60.0


def rate_limiting_enabled() -> bool:
    """Whether per-site rate limiting is active (``[http] rate-enabled``).

    ``--no-rate`` / ``[http] rate-enabled = false`` disables all throttling
    for a run.
    """
    return bool(http_setting("rate-enabled", True))


class RateLimiter:
    """Token-bucket per-host request limiter.

    ``await limiter.acquire(host)`` spaces requests ``1/limit`` seconds
    apart, so fractional rates are honoured exactly (``1.5 req/s`` → one
    request every ~0.67 s) instead of being rounded up to a whole-number
    burst. Defaults come from :data:`_DEFAULT_RATES`, overridable per-host
    via ``[http] rate`` in ``config.toml``.
    """

    def __init__(self, rates: dict[str, float] | None = None) -> None:
        self._enabled = rate_limiting_enabled()
        self._rates: dict[str, float] = {}
        if rates is not None:
            for host, rate in rates.items():
                if isinstance(host, str) and isinstance(rate, (int, float)):
                    self._rates[host.lower()] = float(rate)
        configured = http_setting("rate", {})
        if isinstance(configured, dict):
            for host, rate in configured.items():
                if isinstance(host, str) and isinstance(rate, (int, float)):
                    self._rates[host.lower()] = float(rate)
        for host, rate in _DEFAULT_RATES.items():
            self._rates.setdefault(host, rate)
        # Per-host overrides from [sources."<host>"] rate beat the global
        # [http] rate map and the built-in defaults (most specific wins).
        sources = load_config().get("sources")
        if isinstance(sources, dict):
            for host, table in sources.items():
                if not isinstance(table, dict):
                    continue
                value = table.get("rate")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self._rates[str(host).lower()] = float(value)
        self._next_available: dict[str, float] = {}

    def limit_for(self, host: str) -> float | None:
        """Requests/second for ``host``, or ``None`` when unlimited."""
        if not self._enabled:
            return None
        return self._rates.get((host or "").lower())

    async def acquire(self, host: str, rate: float | None = None) -> None:
        """Stall until a request to ``host`` is permitted under its rate.

        Requests are paced at ``limit`` per second: each acquire reserves a
        slot ``1/limit`` seconds after the previous one for that host. ``rate``
        overrides the configured per-host limit for this single request (e.g.
        cheap page views may run faster than image transfers); it is still
        gated by ``rate-enabled``.
        """
        limit = rate if rate is not None else self.limit_for(host)
        if limit is None or limit <= 0:
            return
        host = (host or "").lower()
        now = time.monotonic()
        # Reuse the last reserved slot unless it has passed, so concurrent
        # callers share one pacing clock instead of each starting a fresh one.
        slot = max(self._next_available.get(host, now), now)
        wait = slot - now
        if wait > 0:
            await asyncio.sleep(min(wait, _MAX_STALL))
        self._next_available[host] = slot + 1.0 / limit


_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    """Shared process-wide rate limiter."""
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


async def await_ratelimit(host: str, rate: float | None = None) -> None:
    """Stall on the shared limiter for ``host`` (no-op when unlimited).

    ``rate`` overrides the host's configured limit for this single request.
    """
    if not host:
        return
    await get_limiter().acquire(host, rate=rate)
