"""Cheap reachability probe for "are we even online?".

curl_cffi reports DNS failures, refused connections, TLS errors and
mid-transfer drops with the same exception family, and the CLI turns all of
them into "Network error. Check your internet connection." That is right
often enough to be misleading the rest of the time: a site that moved, went
down, or started blocking us produces the same message as a dead Wi-Fi link.

So before blaming the user's connection, ask the network a question it can
answer quickly and cheaply, using endpoints that have nothing to do with the
site being scraped.
"""

from __future__ import annotations

import asyncio
import contextlib
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession

from .utils import http_client_args, validate_request_url_async

# Literal addresses rather than hostnames: a DNS outage must not be able to
# fake an offline verdict for every probe at once. 1.1.1.1/8.8.8.8 answer on
# 443/53 in regions where google.com itself is blocked or intercepted.
_TCP_TARGETS = (("1.1.1.1", 443), ("8.8.8.8", 53))
# Both answer in bytes, not kilobytes. This probe is on the critical path of
# every run, so a target that streams a large index (pypi.org/simple/ times out
# against _HTTP_TIMEOUT) is worse than no target at all. google.com is blocked
# or intercepted in some regions; that costs nothing, because a probe that
# loses the race is cancelled rather than waited on.
_HTTP_TARGETS = ("https://www.google.com/generate_204", "https://duckduckgo.com/robots.txt")

_TCP_TIMEOUT = 3.0
_HTTP_TIMEOUT = 5.0

_cached: bool | None = None


def reset_connectivity_cache() -> None:
    """Forget the cached verdict, so the next check probes again."""
    global _cached
    _cached = None


async def _tcp_reachable(host: str, port: int) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            _TCP_TIMEOUT,
        )
    except (TimeoutError, OSError):
        return False
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return True


async def _http_reachable(url: str) -> bool:
    try:
        await validate_request_url_async(url)
        async with AsyncSession(**http_client_args(host=urlparse(url).netloc)) as client:
            await client.get(url, timeout=_HTTP_TIMEOUT)
    except Exception:
        return False
    # Any answer at all proves the network carried a request and a reply, even
    # a 4xx or a captive-portal page. Judging the *content* is the scraper's
    # job, and reading it wrong here would block a run that would have worked.
    return True


async def _probe() -> bool:
    tasks = [
        asyncio.create_task(coro)
        for coro in (
            *(_tcp_reachable(host, port) for host, port in _TCP_TARGETS),
            *(_http_reachable(url) for url in _HTTP_TARGETS),
        )
    ]
    try:
        for finished in asyncio.as_completed(tasks):
            if await finished:
                return True
        return False
    finally:
        # Wait for the first success, not the slowest probe. Gathering to the
        # end would tax every online run with the round trip of whichever
        # target is unreachable from here, and curl_cffi retries on top.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def check_connectivity(*, force: bool = False) -> bool:
    """True when at least one probe reached the internet.

    Probes run concurrently and the first success ends the check, so the cost
    is the fastest round trip rather than the sum or the slowest. The verdict
    is cached for the life of the process: a batch of 30 URLs should not pay
    for 30 checks.

    A false ``True`` is harmless — callers fall through to the error they
    would have shown anyway — while a false ``False`` aborts a run that might
    have worked, so the verdict is deliberately biased toward "online".
    """
    global _cached
    if not force and _cached is not None:
        return _cached
    try:
        _cached = await _probe()
    except Exception:
        # Never let the diagnostic itself fail the command it is inspecting.
        _cached = True
    return _cached
