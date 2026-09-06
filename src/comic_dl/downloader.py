"""Streaming download engine with retries, resume, size caps, and the download pipeline."""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import email.utils
import errno
import inspect
import math
import os
import random
import shutil
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from curl_cffi.requests import AsyncSession

from .config import http_setting
from .errors import DownloadTimeout
from .http import absorb_response_cookies, jar_cookies_kwargs
from .models import ImageItem, PostMetadata
from .rate import await_ratelimit, rate_limiting_enabled
from .ui import (
    DIAGNOSTIC,
    TAG_DOWNLOAD,
    TAG_RETRY,
    TRACE,
    VERBOSITY,
    glyphs,
    http_event,
    print_error,
    print_error_block,
    trace,
    vlog,
)
from .utils import (
    MAGIC_MAX,
    MAX_REDIRECTS,
    RequestBlockedError,
    http_client_args,
    referer_headers,
    resolve_redirect_url_async,
    validate_request_url_async,
    verify_image_bytes,
    verify_image_file,
)

MAX_DOWNLOAD_RETRIES = 3
DOWNLOAD_TIMEOUT = 60
# One chance to re-mint a stale image link from its source page per retry.
REFRESH_TIMEOUT = 30

# Per-host circuit breaker for image transport. After this many consecutive
# transport-level failures (timeouts, connection resets, TLS aborts) a host
# is parked: further images on it fail fast without touching the network and
# land in the normal failed set, so the rerun machinery retries them later.
# Application-level responses (a 200 HTML stub instead of image bytes) are
# deliberately excluded — those are the stale-link case the refresher owns.
HOST_PARK_THRESHOLD = 3
HOST_PARK_SECONDS = 120.0
# Image retry schedule: exponential 2, 4, 8 seconds,
# jittered ±20% on each retry so concurrent failures don't re-synchronize.
BACKOFF_BASE = 2.0
BACKOFF_MAX = 8.0
# ±20% random spread on each retry delay (see _backoff_delay).
BACKOFF_JITTER = 0.2
# Cap on how long a throttled/failed image may delay *other* in-flight
# downloads. The affected image still does its own full backoff; the batch only
# eases off by a short, fixed amount so a few failures can't freeze everything.
SHARED_COOLDOWN_CAP = 2.0
# Hard ceiling for one pipeline, matching the CLI's own --concurrency cap so
# library callers cannot accidentally create an unbounded connection pool.
MAX_PIPELINE_CONCURRENCY = 32
# Per-chapter page-parallel politeness guard (recommended: 5 pages).
# Applied ONLY when per-site rate limiting is disabled
# (--no-rate / [http] rate-enabled=false): without the token bucket, this cap
# is the last line of defense against hammering a site. With rate limiting on
# (the default), the token bucket enforces each host's req/s, so the user's
# configured page concurrency (CLI default 8, cap 32) is honored as-is.
PAGE_PARALLEL_CEILING = 5
# Upper bound for a cover image fetched into memory.
COVER_MAX_BYTES = 25 * 1024 * 1024
# Byte-reporting throttle: flush live totals to the UI at most every ~100 KB
# or ~0.2s, so a fast page streams smooth byte counts without a callback storm.
BYTE_FLUSH_CHUNK = 100 * 1024
BYTE_FLUSH_INTERVAL = 0.2

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class _StreamResponse(Protocol):
    """The subset of a curl_cffi streaming response the engine relies on."""

    status_code: int
    headers: Mapping[str, str]

    def raise_for_status(self) -> None: ...

    def aiter_content(self, chunk_size: int | None = None) -> AsyncIterator[bytes]: ...

    async def aclose(self) -> None: ...


async def _close_response(inner: object) -> None:
    closer = getattr(inner, "aclose", None) or getattr(inner, "close", None)
    if closer is None:
        return
    try:
        result = closer()
        if inspect.isawaitable(result):
            await result
    except Exception:  # nosec B110
        pass


# Bounded retry for general block responses (non-CF challenges).
# Applied at _open_stream / _fetch_once chokepoints so any vendor's block
# gets a short, bounded retry with exponential backoff. CF-specific
# challenge solving is handled separately by the CF solver.
_HUMANE_MAX_RETRIES = 3
_HUMANE_BACKOFF_BASE = 1.0  # seconds
_HUMANE_BACKOFF_MAX = 4.0   # cap


def _humane_backoff_delay(attempt: int) -> float:
    """Exponential backoff with ±20% jitter, capped."""
    delay = min(_HUMANE_BACKOFF_BASE * (2 ** attempt), _HUMANE_BACKOFF_MAX)
    jitter = delay * BACKOFF_JITTER
    return delay + random.uniform(-jitter, jitter)  # nosec B311


async def _retry_blocked(
    fetch: Callable[[], Awaitable[_StreamResponse]],
    url: str,
) -> _StreamResponse:
    """Retry ``fetch()`` up to ``_HUMANE_MAX_RETRIES`` times on general blocks.

    Detects blocks via :func:`classify_block`. For Cloudflare challenges,
    delegates to :func:`handle_challenge` before retrying. For other vendors,
    applies a short bounded retry with exponential backoff. Always respects
    rate limiting via :func:`await_ratelimit`.
    """
    from .antibot import BlockVerdict, classify_block
    from .cf import handle_challenge

    verdict: BlockVerdict | None = None
    for attempt in range(_HUMANE_MAX_RETRIES):
        resp = await fetch()
        status = getattr(resp, "status_code", 200)
        headers = getattr(resp, "headers", None) or {}
        # Read body for classification (limited to 256KB for performance)
        body = ""
        content = getattr(resp, "content", None)
        if content:
            raw = content if isinstance(content, bytes) else b""
            body = raw[:256_000].decode("utf-8", errors="replace")

        verdict = classify_block(status, headers, body=body, url=url)

        # No block → return immediately
        if verdict.vendor == "none":
            return resp

        # Cloudflare challenge → use CF solver
        if verdict.vendor == "cloudflare" and verdict.kind in ("interstitial", "honeypot"):
            host = urlsplit(url).hostname or "unknown"
            trace(f"retry_blocked: CF challenge on {host} (attempt {attempt + 1})")
            if await handle_challenge(url):
                await _close_response(resp)
                # A successful solve cleared the stale cf_clearance and (via
                # the webview solver) harvested a fresh one; the post-solve
                # request must exercise it. It occupies this attempt's own
                # slot, so a solve on the last attempt still gets its fetch —
                # previously it was discarded as the loop fell through to
                # RequestBlockedError.
                resp = await fetch()
                status = getattr(resp, "status_code", 200)
                headers = getattr(resp, "headers", None) or {}
                content = getattr(resp, "content", None)
                raw = content if isinstance(content, bytes) else b""
                body = raw[:256_000].decode("utf-8", errors="replace")
                if classify_block(status, headers, body=body, url=url).vendor == "none":
                    return resp
                await _close_response(resp)
                continue  # still blocked after the solve → next slot
            return resp  # solve failed, return the challenge response

        # General block (non-CF) → bounded retry with backoff
        if verdict.vendor != "none":
            delay = _humane_backoff_delay(attempt)
            retry_after = _retry_after_wait_seconds(headers)
            if retry_after is not None:
                # The host named a wait; honor it (capped) so a rate/5xx
                # block gets the room it asked for instead of a blind
                # 1/2/4s backoff burning the retry budget.
                delay = retry_after
            trace(
                f"retry_blocked: {verdict.vendor} block ({verdict.kind}) on "
                f"{urlsplit(url).hostname} — retry {attempt + 1}/{_HUMANE_MAX_RETRIES} "
                f"in {delay:.1f}s"
            )
            await _close_response(resp)
            await asyncio.sleep(delay)
            continue

    # Exhausted retries — return the last response or raise
    raise RequestBlockedError(
        f"blocked by anti-bot ({verdict.vendor if verdict else 'unknown'}) "
        f"after {_HUMANE_MAX_RETRIES} attempts on {url!r}"
    )


async def _open_stream(
    client: AsyncSession,
    url: str,
    headers: dict[str, str] | None = None,
    log_level: int = TRACE,
    note: str = "",
) -> _StreamResponse:
    """Open a streaming GET on ``url`` following at most ``MAX_REDIRECTS``.

    Every hop — the initial URL and each redirect ``Location`` — is validated by
    :func:`validate_request_url`, so a public URL can never redirect onto a
    loopback/private/metadata address. Returns the final, entered response
    object (which exposes ``status_code``/``headers`` and streamed content),
    or raises :class:`RequestBlockedError`.

    ``log_level`` is chosen by the *caller* — ``TRACE`` for per-image fetches,
    ``DIAGNOSTIC`` for page/cover-level fetches. ``note`` carries a stable
    page identifier (``page_XXXX.webp``) that is echoed on each hop so the
    trace is cross-referable against retry/verify logs. The infrastructure
    only reports at the requested level and never decides semantics itself.
    """
    req = dict(headers or {})
    current = url

    async def _stream_once() -> _StreamResponse:
        nonlocal current
        inner = cast(_StreamResponse, None)
        for _ in range(MAX_REDIRECTS + 1):
            await validate_request_url_async(current)
            await await_ratelimit(current)
            _started = time.monotonic()
            obj = client.stream(
                "GET",
                current,
                headers=req,
                allow_redirects=False,
                **jar_cookies_kwargs(current),
            )
            if inspect.isawaitable(obj):
                obj = await obj
            entered = obj
            enter = getattr(obj, "__aenter__", None)
            if callable(enter):
                _aenter = cast(Callable[[], Awaitable[Any]], enter)
                entered = await _aenter()
            inner = cast(_StreamResponse, entered)
            absorb_response_cookies(client, inner.headers)
            status = inner.status_code
            _elapsed = time.monotonic() - _started
            http_event(
                "GET",
                current,
                status=status,
                duration=_elapsed,
                headers=inner.headers or {},
                level=log_level,
                note=note,
            )
            location = (inner.headers or {}).get("location")
            if status in _REDIRECT_STATUSES and location:
                if callable(getattr(obj, "__aexit__", None)):
                    with contextlib.suppress(Exception):
                        await obj.__aexit__(None, None, None)
                else:
                    await _close_response(inner)
                current = await resolve_redirect_url_async(current, location)
                continue
            return inner
        # Redirect loop past the cap.
        if inner is not None:
            await _close_response(inner)
        raise RequestBlockedError(
            f"too many redirects ({MAX_REDIRECTS}) while following {url!r}"
        )

    return await _retry_blocked(_stream_once, url)


def _existing_matches(path: Path, data: bytes) -> bool:
    """Return True if ``path`` already holds exactly ``data`` (size first)."""
    try:
        if path.stat().st_size != len(data):
            return False
        with open(path, "rb") as f:
            return f.read() == data
    except OSError:
        return False


_SHARED_COVER_LOCK = threading.Lock()
_shared_cover_session: AsyncSession | None = None


def _shared_cover_client() -> AsyncSession:
    """Process-wide lazy cover session (no per-cover warm-up for batches).

    Created without a referer — each cover sends its own ``Referer``/
    ``Origin`` per-request via :func:`download_cover_to`. Never closed
    automatically; the CLI calls :func:`close_shared_cover_session` at exit.
    """
    global _shared_cover_session
    if _shared_cover_session is None:
        with _SHARED_COVER_LOCK:
            if _shared_cover_session is None:
                cover_kwargs: dict[str, Any] = {**http_client_args(), "max_clients": 4}
                _shared_cover_session = AsyncSession(**cover_kwargs)
    return _shared_cover_session


async def close_shared_cover_session() -> None:
    """Best-effort close of the shared cover session (safe to call at exit)."""
    global _shared_cover_session
    session, _shared_cover_session = _shared_cover_session, None
    if session is None:
        return
    with contextlib.suppress(Exception):
        await session.close()


async def download_cover_to(
    url: str,
    dest_path: Path,
    *,
    client: AsyncSession | None = None,
    referer_url: str = "",
    force: bool = False,
    _base_headers: dict[str, str] | None = None,
) -> bool:
    """Fetch a cover image into ``dest_path`` (best-effort, conditional).

    Sends ``If-Modified-Since`` derived from the existing file's mtime so a
    ``304`` reply skips both the body transfer and the rewrite. If the server
    ignores the condition, the fetched bytes are compared against the existing
    file and only a genuinely changed cover is written; ``force=True`` always
    (re)writes. Network/HTTP failures are swallowed — the cover is optional —
    and reported as ``False``; ``True`` means the file was written. When the
    server sends ``Last-Modified`` it is preserved as the file mtime so future
    conditional checks stay as accurate as possible.

    When no ``client`` is given the fetch goes through a process-wide shared
    session (:func:`_shared_cover_client`) so a batch of covers does not pay
    connection-pool warm-up per cover. The per-cover ``Referer``/``Origin`` is
    sent as a per-request header since the shared session cannot carry one
    referer for every source.
    """
    if client is None:
        referer = referer_headers(referer_url) if referer_url else {}
        return await download_cover_to(
            url, dest_path, client=_shared_cover_client(),
            referer_url=referer_url, force=force, _base_headers=referer,
        )
    try:
        headers: dict[str, str] = dict(_base_headers or {})
        # The shared-session path above sends Referer/Origin per-request; the
        # call-provided client also needs them or hotlink-protected covers 403.
        if referer_url:
            headers.update(referer_headers(referer_url))
        if not force and dest_path.exists():
            headers["If-Modified-Since"] = email.utils.formatdate(
                dest_path.stat().st_mtime, usegmt=True
            )

        resp = await _open_stream(
            client, url, headers=headers, log_level=DIAGNOSTIC,
        )
        try:
            if resp.status_code == 304:
                trace("cover: served 304 (unchanged) — skipped")
                return False
            if resp.status_code >= 400:
                vlog(
                    DIAGNOSTIC,
                    f"cover download failed: HTTP {resp.status_code}",
                    tag=TAG_DOWNLOAD,
                )
                return await _session_cover_fallback(
                    url, dest_path, force=force, status=resp.status_code
                )
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_content():
                size += len(chunk)
                if size > COVER_MAX_BYTES:
                    raise ValueError(f"cover exceeds {COVER_MAX_BYTES} bytes")
                chunks.append(chunk)
            data = b"".join(chunks)
            last_modified = (resp.headers or {}).get("last-modified")
        finally:
            await _close_response(resp)

        if not force and _existing_matches(dest_path, data):
            return False

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(data)
        if last_modified:
            try:
                ts = email.utils.parsedate_to_datetime(last_modified)
                os.utime(dest_path, (ts.timestamp(), ts.timestamp()))
            except (TypeError, ValueError, OSError):
                pass
        return True
    except Exception as exc:
        vlog(DIAGNOSTIC, f"cover download failed: {exc}", tag=TAG_DOWNLOAD)
        return False


async def _session_cover_fallback(
    url: str,
    dest_path: Path,
    *,
    force: bool,
    status: int,
) -> bool:
    """Retry a failed cover via the webview session (fingerprint-bound hosts).

    Some sites (kagane.to) 403 even the cover API when replayed over plain
    curl; when a webview request session is enabled, fetch the bytes as an
    in-page XHR instead. Best-effort like the rest of the cover path.
    """
    if status != 403:
        return False
    with contextlib.suppress(Exception):
        from . import webview

        if not webview.session_enabled():
            return False
        resp_status, _, data = await webview.session_request("GET", url)
        if resp_status not in (200, 304) or not data:
            return False
        if not force and _existing_matches(dest_path, data):
            return False
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(data)
        vlog(DIAGNOSTIC, "cover: fetched via webview session", tag=TAG_DOWNLOAD)
        return True
    return False


class InsufficientDiskError(Exception):
    """Raised when a write fails because the disk is full (ENOSPC).

    Unlike other errors, this is never retried and the partial `.part`
    file is preserved so the download can resume once space is freed.
    """


# `.part` files currently on disk (absolute paths). Populated as bytes are
# actually written and cleared when a file is promoted to its final name or
# deleted. The CLI reads this at SIGINT time to decide whether a "Partial
# save kept — rerun to resume" message is truthful. A plain set is fine: the
# event loop and signal handler run in the same thread.
_PARTIAL_PATHS: set[str] = set()


def _mark_partial(path: Path) -> None:
    _PARTIAL_PATHS.add(str(path))


def _clear_partial(path: Path) -> None:
    _PARTIAL_PATHS.discard(str(path))


def active_partial_files() -> set[str]:
    """Absolute paths of resumable ``.part`` files written so far."""
    return set(_PARTIAL_PATHS)


@dataclass(slots=True)
class PipelineResult:
    """Outcome of a pipeline run: success flag, archive path, and failures."""

    ok: bool = False
    cbz_path: Path = Path()
    cbz_size: int = 0
    cbz_pages: int = 0
    error: str = ""
    failed_images: set[str] = field(default_factory=set)
    # Page filename -> why it failed ("HTTP 530", "timed out", "no disk
    # space", ...). Verify-only faults are prefixed "verification: ".
    failed_reasons: dict[str, str] = field(default_factory=dict)



# --- Retry helpers ---

# 509 is e-hentai's H@H "Bandwidth Limit Exceeded" throttle response, served
# by the image nodes when a client asks for too much, too fast. Without it in
# this set a throttled image fails immediately instead of backing off.  408 is
# a generic CDN request-timeout (common on overloaded manga hosts) and 530 is
# Cloudflare's "origin error" — both transient; both must back off and retry
# instead of killing every page of a chapter in one pass.
RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504, 509, 530})

# Ceiling on how long a server's ``Retry-After`` may pause one image. A host
# can ask for an hour; the capped delay keeps the run finishing while the
# shared cooldown still paces the batch, and a genuinely throttled host is
# parked by the breaker or retried on the rerun rather than blocking forever.
RETRY_AFTER_CAP = 30.0


class NotImageResponseError(Exception):
    """An image URL answered with a body that is not an image.

    Raised when an endpoint (e.g. an e-hentai throttle page) serves HTML under
    HTTP 200 where image bytes belong. Retryable: the failure is transient
    throttling, not a bad URL, and the shared cooldown should engage so the
    retry lands after the server eases up.
    """

    def __init__(self, filename: str) -> None:
        self.filename = filename
        super().__init__(f"Response for {filename} is not an image")


def _backoff_delay(
    attempt: int,
    base: float = BACKOFF_BASE,
    max_delay: float = BACKOFF_MAX,
    *,
    jitter: bool = False,
) -> float:
    """Exponential ``2, 4, 8``s retry backoff, optionally jittered ±20%.

    ``jitter=True`` randomizes each scheduled delay to
    ``delay * U(1 - 0.2, 1 + 0.2)`` so concurrently-failing tasks do not
    retry in lockstep (thundering herd). The default stays deterministic for
    tests and callers that want exact scheduling.
    """
    delay = min(base * (2 ** attempt), max_delay)
    if jitter:
        # Retry-spacing jitter is not a security boundary (secrets.py would be
        # wrong here); deterministic `random` is exactly right for de-syncing
        # concurrent retries.
        delay *= random.uniform(1 - BACKOFF_JITTER, 1 + BACKOFF_JITTER)  # nosec B311
    return delay


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


# host -> {"fails": int, "parked_until": monotonic}
_host_breaker: dict[str, dict[str, float]] = {}
_host_breaker_lock = threading.Lock()


def reset_host_breaker() -> None:
    """Clear parked-host state (new run / tests)."""
    with _host_breaker_lock:
        _host_breaker.clear()


def host_parked(host: str, now: float) -> bool:
    """True while ``host`` is inside its post-failure parking window."""
    with _host_breaker_lock:
        st = _host_breaker.get(host)
        if st is None:
            return False
        return st["parked_until"] > now


def record_transport_success(host: str) -> None:
    """Reset the consecutive-failure count so isolated failures never park."""
    if not host:
        return
    with _host_breaker_lock:
        st = _host_breaker.get(host)
        if st is not None:
            st["fails"] = 0


def record_transport_failure(host: str, now: float) -> None:
    """Count one transport failure; park the host on the Nth consecutive."""
    if not host:
        return
    with _host_breaker_lock:
        st = _host_breaker.setdefault(host, {"fails": 0.0, "parked_until": 0.0})
        st["fails"] += 1
        if st["fails"] >= HOST_PARK_THRESHOLD:
            st["parked_until"] = now + HOST_PARK_SECONDS
            st["fails"] = 0.0
            vlog(
                DIAGNOSTIC,
                f"breaker: parking {host} for {HOST_PARK_SECONDS:.0f}s "
                f"after {HOST_PARK_THRESHOLD} transport failures",
                tag=TAG_RETRY,
            )


def _is_transport_failure(exc: Exception) -> bool:
    """True for network-level faults; False for application-level replies.

    A 200 HTML stub where image bytes were expected is an expired/stale
    link — the refresher's problem — and must never park a node that may
    be perfectly healthy for fresh links.
    """
    from curl_cffi.requests.exceptions import ConnectionError, HTTPError, Timeout

    if isinstance(exc, asyncio.CancelledError):
        return False
    if isinstance(exc, NotImageResponseError):
        return False
    if isinstance(exc, RequestBlockedError):
        # A host that answers every attempt with a WAF/anti-bot block is, for
        # our purposes, a dead node: let the breaker park it so the remaining
        # pages fail fast instead of each burning the full humane-retry run.
        return True
    if isinstance(exc, HTTPError):
        # An HTTP status is an application-level reply, not a transport fault
        # — three 404s must not park an otherwise healthy host.
        return False
    if isinstance(exc, (Timeout, DownloadTimeout, ConnectionError)):
        return True
    # Raw CurlError surfaces for TLS aborts / low-speed kills; its .code is
    # the libcurl error number when present.
    code = getattr(exc, "code", None)
    return isinstance(code, int)


def _is_retryable(exc: Exception) -> bool:
    from curl_cffi.requests.exceptions import ConnectionError, HTTPError, Timeout

    if isinstance(exc, asyncio.CancelledError):
        return False
    if isinstance(exc, InsufficientDiskError):
        return False
    if isinstance(exc, NotImageResponseError):
        return True
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return False
    if isinstance(exc, HTTPError):
        code = exc.response.status_code if exc.response is not None else 0
        if code == 0:
            return True
        return code in RETRYABLE_STATUSES
    if isinstance(exc, (ConnectionError, Timeout)):
        return True
    if isinstance(exc, DownloadTimeout):
        return True
    return isinstance(exc, OSError)


def _download_failure_label(exc: Exception) -> str:
    """Short human label for why one page failed to download.

    Prefer the concrete fault (``HTTP 530``, ``timed out``) over the bare
    ``missing`` the verify step would infer from the file not existing, so a
    failing run reports *why*, not just *that*.
    """
    from curl_cffi.requests.exceptions import ConnectionError, HTTPError, Timeout

    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    if isinstance(exc, RequestBlockedError):
        return "blocked"
    if isinstance(exc, HTTPError):
        code = exc.response.status_code if exc.response is not None else None
        if isinstance(code, int):
            return f"HTTP {code}"
        return "HTTP error"
    if isinstance(exc, (Timeout, DownloadTimeout)):
        return "timed out"
    if isinstance(exc, ConnectionError):
        return "connection failed"
    return type(exc).__name__


def _retry_after_wait_seconds(headers: Mapping[str, Any] | None) -> float | None:
    """Seconds a ``Retry-After`` header asks us to wait, or ``None``.

    Reads an RFC 7231 ``Retry-After`` header — delta-seconds or an HTTP-date.
    ``None`` when the header is absent, malformed, or already in the past, so
    the exponential backoff stays the default. Values beyond
    :data:`RETRY_AFTER_CAP` are capped; ``None`` is also returned for
    non-finite values.
    """
    if not headers:
        return None
    raw = next(
        (v for k, v in headers.items() if k.lower() == "retry-after"), None
    )
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        try:
            parsed = email.utils.parsedate_to_datetime(str(raw))
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.UTC)
        seconds = (parsed - datetime.datetime.now(datetime.UTC)).total_seconds()
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    return min(seconds, RETRY_AFTER_CAP)


async def _refresh_stale_link(client: AsyncSession, item: ImageItem) -> ImageItem | None:
    """Re-mint ``item.url`` from its source page when the link went stale.

    Time-limited image links (e-hentai H@H keystamps) cannot succeed on a
    blind retry once expired; the refresher re-fetches the page that issued
    the link and returns a fresh :class:`ImageItem`. Any failure — no
    refresher registered for the host, network error, same URL re-issued —
    yields ``None`` and the caller retries the original link unchanged.
    """
    try:
        # Lazy import: keeps the downloader decoupled from scraper modules.
        from .scrapers.refresh import refresh_image_url

        refreshed = await asyncio.wait_for(
            refresh_image_url(client, item), timeout=REFRESH_TIMEOUT
        )
    except TimeoutError:
        trace(f"refresh: {item.filename} — source-page fetch timed out")
        return None
    except Exception as exc:
        trace(f"refresh: {item.filename} — {type(exc).__name__}")
        return None
    if refreshed is not None:
        trace(f"refresh: {item.filename} — fresh image link from source page")
    return refreshed


# --- image download engine (curl_cffi) ---

async def _try_resume(
    item: ImageItem,
    part_path: Path,
    client: AsyncSession,
    max_image_size: int = 100 * 1024 * 1024,
) -> bool | None:
    if not part_path.exists():
        return None
    existing_size = part_path.stat().st_size
    if existing_size == 0:
        part_path.unlink(missing_ok=True)
        return None

    if existing_size > max_image_size:
        part_path.unlink(missing_ok=True)
        return None

    headers = {"Range": f"bytes={existing_size}-"}
    resp = None
    try:
        resp = await _open_stream(client, item.url, headers=headers)
        if resp.status_code == 206:
            written = existing_size
            with open(part_path, "ab") as f:
                async for chunk in resp.aiter_content():
                    written += len(chunk)
                    if written > max_image_size:
                        part_path.unlink(missing_ok=True)
                        _clear_partial(part_path)
                        return None
                    f.write(chunk)
                    _mark_partial(part_path)
            fmt = verify_image_file(part_path)
            return True if fmt is not None else None
        elif resp.status_code == 416:
            # The server has no bytes from our offset — the partial is stale
            # or corrupted (delete the partial and restart).
            part_path.unlink(missing_ok=True)
            _clear_partial(part_path)
            return None
        part_path.unlink(missing_ok=True)
        _clear_partial(part_path)
        return None
    except Exception:
        part_path.unlink(missing_ok=True)
        _clear_partial(part_path)
        return None
    finally:
        if resp is not None:
            await _close_response(resp)


async def _stream_to_disk(
    item: ImageItem,
    dest: Path,
    client: AsyncSession,
    max_image_size: int = 100 * 1024 * 1024,
    bytes_cb: Callable[[int], None] | None = None,
) -> str | None:
    """Stream ``item.url`` into ``dest``, returning the sniffed image format.

    Returns the detected format (e.g. ``"jpg"``) when the leading magic bytes
    were verified during the stream, or ``None`` for files too short to sniff
    (those are re-verified by :func:`verify_downloads`). Raising
    :class:`NotImageResponseError` for a throttled HTML reply is unchanged.
    """
    resp = None
    head_fmt: str | None = None
    try:
        resp = await _open_stream(client, item.url, note=item.filename)
        resp.raise_for_status()
        content_length = resp.headers.get("content-length")
        if content_length is not None:
            size = int(content_length)
            if size > max_image_size:
                raise ValueError(
                    f"Image too large ({size / 1024 / 1024:.1f} MB, "
                    f"max {max_image_size / 1024 / 1024:.0f} MB)"
                )
        written = 0
        pending = 0
        last_flush = time.monotonic()
        head = bytearray()
        with open(dest, "wb") as f:
            async for chunk in resp.aiter_content():
                written += len(chunk)
                if written > max_image_size:
                    raise ValueError(
                        f"Image exceeded max size ({max_image_size / 1024 / 1024:.0f} MB)"
                    )
                # Sniff the leading magic bytes before committing to disk: a
                # throttled endpoint (e-hentai H@H) can answer an image URL
                # with an HTML throttle page under HTTP 200. Writing it would
                # only surface as "invalid image" at verification time, with no
                # retry. Raise a retryable error here instead so the download
                # backs off and retries while the server eases up.
                if len(head) < MAGIC_MAX:
                    head.extend(chunk[: MAGIC_MAX - len(head)])
                    if len(head) >= MAGIC_MAX:
                        head_fmt = verify_image_bytes(bytes(head))
                        if head_fmt is None:
                            raise NotImageResponseError(item.filename)
                f.write(chunk)
                _mark_partial(dest)
                if bytes_cb is not None:
                    pending += len(chunk)
                    now = time.monotonic()
                    if (
                        pending >= BYTE_FLUSH_CHUNK
                        or now - last_flush >= BYTE_FLUSH_INTERVAL
                    ):
                        bytes_cb(pending)
                        pending = 0
                        last_flush = now
        if bytes_cb is not None and pending:
            bytes_cb(pending)
        return head_fmt
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise InsufficientDiskError(
                f"No space left on device while writing {dest.name}"
            ) from exc
        raise
    finally:
        if resp is not None:
            await _close_response(resp)


async def _aiter_list(items: list[ImageItem]) -> AsyncIterator[ImageItem]:
    """Identity async iterator over a list (non-streaming mode)."""
    for item in items:
        yield item


async def download_httpx(
    images: list[ImageItem],
    dest_dir: Path,
    concurrency: int = 5,
    progress_cb: Callable[[int], None] | None = None,
    client: AsyncSession | None = None,
    max_image_size: int = 100 * 1024 * 1024,
    max_total_size: int = 0,
    bytes_cb: Callable[[int], None] | None = None,
    activity_cb: Callable[[str], None] | None = None,
    stream_formats: dict[str, str] | None = None,
    *,
    download_timeout: float = DOWNLOAD_TIMEOUT,
    max_attempts: int = MAX_DOWNLOAD_RETRIES,
    failure_labels: dict[str, str] | None = None,
) -> set[str]:
    """Download every image in ``images`` to ``dest_dir`` with a concurrency cap.

    Returns the set of filenames that failed to download (empty on success).
    When ``client`` is omitted a short-lived session is created and closed.
    ``stream_formats``, when given, is filled with ``{filename: format}`` for
    pages whose magic bytes were already sniffed during streaming so the
    caller can skip re-verifying them.
    """
    sem = asyncio.Semaphore(concurrency)

    if client is None:
        _session_kwargs: dict[str, Any] = http_client_args()
        if images:
            from urllib.parse import urlsplit
            first_host = urlsplit(images[0].url).hostname
            if first_host:
                _session_kwargs = http_client_args(host=first_host)
        _session_kwargs["max_clients"] = concurrency + 2
        async with AsyncSession(**_session_kwargs) as _client:
            failed, _ = await _run_downloads(
                _aiter_list(images), dest_dir, sem, progress_cb, _client,
                max_image_size, max_total_size, bytes_cb,
                total_pages=len(images), activity_cb=activity_cb,
                stream_formats=stream_formats,
                download_timeout=download_timeout, max_attempts=max_attempts,
                failure_labels=failure_labels,
            )
            return failed
    else:
        failed, _ = await _run_downloads(
            _aiter_list(images), dest_dir, sem, progress_cb, client,
            max_image_size, max_total_size, bytes_cb,
            total_pages=len(images), activity_cb=activity_cb,
            stream_formats=stream_formats,
            download_timeout=download_timeout, max_attempts=max_attempts,
            failure_labels=failure_labels,
        )
        return failed


async def download_httpx_iter(
    images: AsyncIterator[ImageItem],
    dest_dir: Path,
    concurrency: int = 5,
    progress_cb: Callable[[int], None] | None = None,
    client: AsyncSession | None = None,
    max_image_size: int = 100 * 1024 * 1024,
    max_total_size: int = 0,
    bytes_cb: Callable[[int], None] | None = None,
    activity_cb: Callable[[str], None] | None = None,
    stream_formats: dict[str, str] | None = None,
    *,
    download_timeout: float = DOWNLOAD_TIMEOUT,
    max_attempts: int = MAX_DOWNLOAD_RETRIES,
    failure_labels: dict[str, str] | None = None,
) -> tuple[set[str], list[ImageItem]]:
    """Stream downloads from ``images``, returning ``(failed, resolved)``.

    ``resolved`` is the ordered list of ``ImageItem``s the iterator yielded
    (the download side may produce files out of order, but the caller needs
    the canonical page order for verification and archiving).
    """
    sem = asyncio.Semaphore(concurrency)

    if client is None:
        _session_kwargs: dict[str, Any] = http_client_args()
        _session_kwargs["max_clients"] = concurrency + 2
        async with AsyncSession(**_session_kwargs) as _client:
            return await _run_downloads(
                images, dest_dir, sem, progress_cb, _client,
                max_image_size, max_total_size, bytes_cb, activity_cb=activity_cb,
                stream_formats=stream_formats,
                download_timeout=download_timeout, max_attempts=max_attempts,
                failure_labels=failure_labels,
            )
    return await _run_downloads(
        images, dest_dir, sem, progress_cb, client,
        max_image_size, max_total_size, bytes_cb, activity_cb=activity_cb,
        stream_formats=stream_formats,
        download_timeout=download_timeout, max_attempts=max_attempts,
        failure_labels=failure_labels,
    )


async def _run_downloads(
    items: AsyncIterator[ImageItem],
    dest_dir: Path,
    sem: asyncio.Semaphore,
    progress_cb: Callable[[int], None] | None,
    client: AsyncSession,
    max_image_size: int = 100 * 1024 * 1024,
    max_total_size: int = 0,
    bytes_cb: Callable[[int], None] | None = None,
    activity_cb: Callable[[str], None] | None = None,
    total_pages: int | None = None,
    stream_formats: dict[str, str] | None = None,
    reasons: dict[str, int] | None = None,
    failure_labels: dict[str, str] | None = None,
    *,
    download_timeout: float = DOWNLOAD_TIMEOUT,
    max_attempts: int = MAX_DOWNLOAD_RETRIES,
) -> tuple[set[str], list[ImageItem]]:
    # Shared adaptive throttle: when any download hits a retryable failure,
    # all in-flight downloads pause until the backoff window elapses, so the
    # request rate drops under site throttling instead of hammering harder.
    cooldown_until: float = 0.0
    # Cumulative hard cap on total bytes accepted across all downloads (0 = unlimited).
    consumed_bytes = [0]
    completed = 0
    failed: set[str] = set()
    # Items the iterator produced, in yield (page) order.
    resolved: list[ImageItem] = []
    # Optional counter of failure reason labels (e.g. "transport", "blocked")
    # recorded when a download gives up after exhausting retries.
    if reasons is None:
        reasons = {}
    # Per-page human reason ("HTTP 530") surfaced in the final report so a
    # failure reads as a cause, not just a missing file.
    if failure_labels is None:
        failure_labels = {}

    def _budget_exhausted() -> bool:
        return 0 < max_total_size <= consumed_bytes[0]

    async def _download_one(
        item: ImageItem,
        stream_formats: dict[str, str] | None = None,
    ) -> None:
        nonlocal completed, cooldown_until
        dest = (dest_dir / item.filename).resolve()
        part_path = (dest_dir / f"{item.filename}.part").resolve()
        dest_dir_resolved = dest_dir.resolve()
        try:
            dest.relative_to(dest_dir_resolved)
            part_path.relative_to(dest_dir_resolved)
        except ValueError:
            failed.add(item.filename)
            failure_labels[item.filename] = "unsafe file name"
            reasons["budget"] = reasons.get("budget", 0) + 1
            completed += 1
            if progress_cb:
                progress_cb(completed)
            return

        # Hard total-size gate: refuse to start further work once the budget is
        # consumed by already-accepted images.
        if _budget_exhausted():
            failed.add(item.filename)
            failure_labels[item.filename] = "exceeds max total size"
            reasons["budget"] = reasons.get("budget", 0) + 1
            completed += 1
            if progress_cb:
                progress_cb(completed)
            return

        # If destination already exists and is valid, skip download
        if dest.exists() and dest.stat().st_size > 0:
            fmt = verify_image_file(dest)
            if fmt is not None:
                completed += 1
                if progress_cb:
                    progress_cb(completed)
                return

        # Current attempt's peer; rebinds when a stale link is refreshed to
        # a different node. Pre-bound so failure handlers never see it unset.
        host: str = _host_of(item.url)

        for attempt in range(max_attempts):
            if activity_cb is not None:
                if total_pages:
                    activity_cb(f"Downloading {completed + 1}/{total_pages}")
                else:
                    activity_cb(f"Downloading images{glyphs().ellipsis}")
            if _budget_exhausted():
                failed.add(item.filename)
                failure_labels[item.filename] = "exceeds max total size"
                reasons["budget"] = reasons.get("budget", 0) + 1
                completed += 1
                if progress_cb:
                    progress_cb(completed)
                return
            try:
                loop = asyncio.get_running_loop()
                now = loop.time()
                if cooldown_until > now:
                    if activity_cb is not None:
                        activity_cb(f"Waiting for server{glyphs().ellipsis}")
                    await asyncio.sleep(
                        min(cooldown_until - now, SHARED_COOLDOWN_CAP)
                    )
                host = _host_of(item.url)
                if host_parked(host, loop.time()):
                    # Parked node: fail fast WITHOUT taking a semaphore slot
                    # or opening a socket; rerun picks the page back up.
                    part_path.unlink(missing_ok=True)
                    _clear_partial(part_path)
                    failed.add(item.filename)
                    failure_labels[item.filename] = "host parked (cooldown)"
                    reasons["parked"] = reasons.get("parked", 0) + 1
                    completed += 1
                    if progress_cb:
                        progress_cb(completed)
                    vlog(
                        DIAGNOSTIC,
                        f"image {item.filename} skipped: {host} is parked "
                        f"({HOST_PARK_SECONDS:.0f}s cooldown)",
                        tag=TAG_RETRY,
                    )
                    return
                async with sem:
                    if part_path.exists() and part_path.stat().st_size > 0:
                        # Resume from the partial's byte offset regardless of
                        # whether it already forms a complete image: a dropped
                        # transfer is precisely the case Range continuation
                        # exists for. _try_resume validates the finished bytes,
                        # and a stale/corrupt partial (416, non-206 reply, or
                        # bad tail) falls through to a fresh full download.
                        _mark_partial(part_path)
                        trace(
                            f"resume: {item.filename} — {part_path.stat().st_size} "
                            f"bytes of partial data, continuing{glyphs().ellipsis}"
                        )
                        resumed = await _try_resume(
                            item, part_path, client, max_image_size
                        )
                        if resumed is True:
                            size = part_path.stat().st_size
                            consumed_bytes[0] += size
                            if bytes_cb:
                                bytes_cb(size)
                            _clear_partial(part_path)
                            part_path.rename(dest)
                            record_transport_success(host)
                            completed += 1
                            if progress_cb:
                                progress_cb(completed)
                            return
                        part_path.unlink(missing_ok=True)
                        _clear_partial(part_path)
                    elif part_path.exists():
                        part_path.unlink(missing_ok=True)
                        _clear_partial(part_path)

                    try:
                        fmt = await asyncio.wait_for(
                            _stream_to_disk(
                                item, part_path, client, max_image_size, bytes_cb
                            ),
                            timeout=download_timeout,
                        )
                    except TimeoutError as e:
                        raise DownloadTimeout(
                            item.filename, download_timeout
                        ) from e
                size = part_path.stat().st_size
                consumed_bytes[0] += size
                _clear_partial(part_path)
                part_path.rename(dest)
                record_transport_success(host)
                if fmt is not None and stream_formats is not None:
                    stream_formats[item.filename] = fmt
                completed += 1
                if progress_cb:
                    progress_cb(completed)
                return
            except asyncio.CancelledError:
                part_path.unlink(missing_ok=True)
                _clear_partial(part_path)
                raise
            except InsufficientDiskError:
                # The partial is preserved on disk for a later resume — keep it
                # tracked so an interrupt still reports resumable data.
                failed.add(item.filename)
                failure_labels[item.filename] = "no disk space"
                reasons["disk"] = reasons.get("disk", 0) + 1
                completed += 1
                if progress_cb:
                    progress_cb(completed)
                return
            except ValueError:
                part_path.unlink(missing_ok=True)
                _clear_partial(part_path)
                failed.add(item.filename)
                failure_labels[item.filename] = "invalid image data"
                reasons["value"] = reasons.get("value", 0) + 1
                completed += 1
                if progress_cb:
                    progress_cb(completed)
                return
            except Exception as exc:
                if _is_transport_failure(exc):
                    record_transport_failure(
                        host, asyncio.get_running_loop().time()
                    )
                if not _is_retryable(exc) or attempt >= max_attempts - 1:
                    part_path.unlink(missing_ok=True)
                    _clear_partial(part_path)
                    failed.add(item.filename)
                    failure_labels[item.filename] = _download_failure_label(exc)
                    reasons["transport"] = reasons.get("transport", 0) + 1
                    completed += 1
                    if progress_cb:
                        progress_cb(completed)
                    vlog(
                        DIAGNOSTIC,
                        f"image {item.filename} failed: {exc}",
                        tag=TAG_DOWNLOAD,
                    )
                    return
                vlog(
                    DIAGNOSTIC,
                    f"image {item.filename} — attempt {attempt + 2}/{max_attempts}",
                    tag=TAG_RETRY,
                )
                retry_after = _retry_after_wait_seconds(
                    getattr(getattr(exc, "response", None), "headers", None)
                )
                if retry_after is not None:
                    # The server named a time; honor it (capped) so a 429/503
                    # with a long Retry-After gets the room it asked for
                    # instead of a blind 2/4/8s backoff burning the budget.
                    # Retry-After is authoritative, so no jitter is applied.
                    delay = retry_after
                else:
                    delay = _backoff_delay(attempt, jitter=True)
                # A time-limited link (keystamp) that just failed may be
                # expired: re-mint it from its source page so the retry
                # targets a live URL instead of the same dead one.
                if item.source_url:
                    refreshed = await _refresh_stale_link(client, item)
                    if refreshed is not None:
                        item = refreshed
                trace(
                    f"retry {item.filename}: attempt {attempt + 2}/{max_attempts} "
                    f"after {type(exc).__name__}; backoff {delay:.1f}s"
                )
                cooldown_until = max(
                    cooldown_until,
                    asyncio.get_running_loop().time() + delay,
                )
                await asyncio.sleep(delay)

    async def _produce() -> None:
        # asyncio.TaskGroup (Python ≥3.11): structured concurrency — the group
        # awaits every task and cancels stragglers if one fails, so no orphaned
        # download task can leak past the batch.
        async with asyncio.TaskGroup() as tg:
            async for item in items:
                resolved.append(item)
                tg.create_task(_download_one(item, stream_formats))

    await _produce()
    return failed, resolved


# --- Verification ---

def verify_downloads(
    images: list[ImageItem],
    dest_dir: Path,
    known_formats: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Check every expected page file is present, non-empty, and a real image.

    Files listed in ``known_formats`` (``{filename: format}`` sniffed during
    the download stream) skip the header re-read — the magic bytes were
    already validated while writing. Everything else is verified by
    :func:`verify_image_file`.

    Returns ``(errors, verified)`` where ``errors`` maps filename to a reason
    (``"missing"`` / ``"empty"`` / ``"invalid image"``) and ``verified`` maps
    filename to its detected format. Empty and invalid files are deleted so a
    later retry re-fetches them instead of trusting a bad download.
    """
    errors: dict[str, str] = {}
    verified: dict[str, str] = {}
    if not dest_dir.exists():
        return {item.filename: f"directory not found ({dest_dir})" for item in images}, {}
    for item in images:
        path = dest_dir / item.filename
        if not path.exists():
            errors[item.filename] = "missing"
            continue
        if path.stat().st_size == 0:
            errors[item.filename] = "empty"
            path.unlink(missing_ok=True)
            continue
        fmt = (known_formats or {}).get(item.filename)
        if fmt is None:
            fmt = verify_image_file(path)
        if fmt is None:
            errors[item.filename] = "invalid image"
            path.unlink(missing_ok=True)
            continue
        verified[item.filename] = fmt
    return errors, verified


# --- Size probe (informational only) ---

async def probe_download_size(
    images: list[ImageItem],
    referer_url: str | None = None,
    *,
    sample_size: int = 8,
    concurrency: int = 8,
    timeout: float = 5.0,
) -> int:
    """Best-effort estimate of total download bytes using the `content-length`
    of a sample of pages (median x page count).

    Purely informational: callers must treat the result as advisory and never
    gate on it. Returns 0 when the size cannot be determined — in particular
    when fewer than three probes in the sample report a size, or the whole
    probe batch exceeds ``timeout``. Sites that omit ``content-length`` /
    ``content-range`` headers therefore produce no estimate.
    """
    if not images:
        return 0
    sample = images[:sample_size]
    sizes: list[int] = []
    client_kwargs: dict[str, Any] = {
        **http_client_args(referer_url=referer_url),
        "max_clients": concurrency + 2,
    }
    try:
        async with AsyncSession(**client_kwargs) as c:
            sem = asyncio.Semaphore(concurrency)

            async def _probe_one(item: ImageItem) -> None:
                async with sem:
                    size = await _probe_image_size(c, item.url, timeout)
                    if size > 0:
                        sizes.append(size)

            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(
                    asyncio.gather(*(_probe_one(i) for i in sample)),
                    timeout=timeout,
                )
    except Exception:
        return 0

    if len(sizes) < 3:
        return 0
    sizes.sort()
    median = sizes[len(sizes) // 2]
    return int(median * len(images))


async def _probe_image_size(c: AsyncSession, url: str, timeout: float) -> int:
    try:
        await validate_request_url_async(url)
    except RequestBlockedError:
        return 0
    try:
        resp = await asyncio.wait_for(c.head(url), timeout=timeout)
        content_length = resp.headers.get("content-length")
        if content_length and content_length.isdigit():
            return int(content_length)
    except Exception:  # nosec B110
        pass

    try:
        resp = await asyncio.wait_for(
            c.get(url, headers={"Range": "bytes=0-0"}, stream=True),
            timeout=timeout,
        )
        try:
            content_range = resp.headers.get("content-range")
            if content_range and "/" in content_range:
                total = content_range.rsplit("/", 1)[1]
                if total.isdigit():
                    return int(total)
            content_length = resp.headers.get("content-length")
            if content_length and content_length.isdigit():
                return int(content_length)
        finally:
            with contextlib.suppress(Exception):
                await resp.aclose()
    except Exception:  # nosec B110
        pass
    return 0


# --- Download Pipeline ---

def _engine_tuning() -> tuple[float, int]:
    """Per-image hard timeout and attempt budget from ``[http]`` config.

    ``[http] download-timeout`` (seconds, default :data:`DOWNLOAD_TIMEOUT`)
    bounds a single image fetch; ``[http] download-retries`` (default
    ``MAX_DOWNLOAD_RETRIES - 1``) is the number of retries *after* the first
    attempt, so the total attempt budget is ``download-retries + 1``. Values
    are clamped (1-600 s, 0-10 retries) so a bad value can neither hang a
    download forever nor give up at zero attempts. Read once per pipeline —
    not per image — so the engine never re-reads the config mid-chapter.
    """
    timeout: float = DOWNLOAD_TIMEOUT
    retries: int = MAX_DOWNLOAD_RETRIES - 1
    with contextlib.suppress(TypeError, ValueError):
        timeout = float(http_setting("download-timeout", DOWNLOAD_TIMEOUT))
        retries = int(http_setting("download-retries", MAX_DOWNLOAD_RETRIES - 1))
    retries = min(max(retries, 0), 10)
    timeout = min(max(timeout, 1.0), 600.0)
    return timeout, retries + 1


class StatusSink(Protocol):
    """Consumer that renders a live status row for one download pipeline.

    Implemented by ``_RowSink`` (a row of an :class:`~comic_dl.ui.Activity`).
    ``succeed`` / ``fail`` retire the row and print a durable result line.
    """

    def stage(self, text: str) -> None: ...

    def set_activity(self, text: str) -> None: ...

    def show_progress(self, total: int) -> None: ...

    def update_progress(self, done: int) -> None: ...

    def add_bytes(self, n: int) -> None: ...

    def clear_progress(self) -> None: ...

    async def succeed(self, message: str) -> None: ...

    async def fail(self, message: str) -> None: ...


async def _announce_pack(
    series_prefix: str,
    state: dict[str, int],
    stage: Callable[[str], Awaitable[None]],
    pack_task: asyncio.Task,
) -> None:
    """Live-update the archive stage text while the archiver packs pages.

    Polls a cheap counter mutated by the packing thread and refreshes the
    stage line as pages are written; fast packs never tick, so the static
    "Creating CBZ archive..." line (or spinner) is unaffected until the pack
    has been running for at least one interval.
    """
    last = -1
    while True:
        n = state["n"]
        if n != last and n:
            await stage(
                f"{series_prefix}Creating CBZ archive... {n}/{state['total']} pages"
            )
            last = n
        if pack_task.done():
            return
        await asyncio.sleep(0.1)


class DownloadPipeline:
    """Orchestrates one download run: fetch, verify, and package a CBZ.

    Owns the temp directory, the progress UI, and the final archive. Use as an
    async context manager so the working directory is cleaned up regardless of
    outcome.
    """

    def __init__(
        self,
        images: list[ImageItem],
        tmp_dir: Path,
        cbz_path: Path,
        *,
        series_title: str = "",
        chapter_title: str = "",
        url: str = "",
        chapter_number: str | None = None,
        volume_number: str | None = None,
        series_meta: PostMetadata | None = None,
        concurrency: int = 5,
        max_image_size: int = 100 * 1024 * 1024,
        max_total_size: int = 0,
        referer_url: str | None = None,
        quiet: bool = False,
        client: AsyncSession | None = None,
        status_sink: StatusSink | None = None,
        images_iter: AsyncIterator[ImageItem] | None = None,
        total_pages: int | None = None,
        compression: str = "stored",
    ):
        self._images = images
        # Streaming mode: images arrive from ``images_iter`` as URLs resolve,
        # overlapping URL resolution with downloads. ``_resolved_images`` is
        # filled by the download step with the yielded (page-ordered) items.
        self._images_iter = images_iter
        self._total_pages = total_pages
        self._resolved_images: list[ImageItem] = []
        self._stream_formats: dict[str, str] = {}
        # Filename -> "HTTP 530" / "timed out" / ... ; filled by the download
        # step and merged with verify-only faults in ``run``.
        self._failure_labels: dict[str, str] = {}
        self._tmp_dir = tmp_dir
        self._cbz_path = cbz_path
        self._series_title = series_title
        self._chapter_title = chapter_title
        self._url = url
        self._chapter_number = chapter_number
        self._volume_number = volume_number
        self._series_meta = series_meta
        # Page-parallel policy: honor the caller's value (CLI default 8, cap
        # 32) whenever per-site rate limiting is active — the token bucket
        # already enforces politeness, so extra depth only queues work, it
        # does not raise request rate. When rate limiting is disabled the
        # 5-page ceiling (PAGE_PARALLEL_CEILING) applies as the last
        # politeness guard. ``_concurrency_clamped`` lets the CLI emit one
        # explicit notice per run when the ceiling downgrades the request.
        requested = max(1, int(concurrency or 1))
        if rate_limiting_enabled():
            self._concurrency = min(requested, MAX_PIPELINE_CONCURRENCY)
        else:
            self._concurrency = min(
                requested, MAX_PIPELINE_CONCURRENCY, PAGE_PARALLEL_CEILING
            )
        self._concurrency_clamped = self._concurrency < requested
        self._max_image_size = max_image_size
        self._max_total_size = max_total_size
        self._referer_url = referer_url
        self._quiet = quiet
        self._client = client
        self._status_sink = status_sink
        self._bytes_cb: Callable[[int], None] | None = None
        self._compression = compression
        self._download_timeout, self._max_attempts = _engine_tuning()

    # ── public API ──────────────────────────────────────────────

    def _page_total(self) -> int:
        """Number of pages the pipeline will produce (progress denominator)."""
        if self._images_iter is not None:
            return self._total_pages or 0
        return len(self._images)

    def _current_images(self) -> list[ImageItem]:
        """Canonical page-ordered image list for verify/archive."""
        if self._images_iter is not None:
            return self._resolved_images
        return self._images

    async def run(self, series_prefix: str = "") -> PipelineResult:
        from .archiver import create_archive
        from .ui import Pipeline

        sink = self._status_sink
        pipe = None if sink is not None else Pipeline(quiet=self._quiet)

        async def stage(text: str) -> None:
            if sink is not None:
                sink.stage(text)
            elif pipe is not None:
                pipe.stage(text)

        async def show_progress(total: int) -> None:
            if sink is not None:
                sink.show_progress(total)
            elif pipe is not None:
                pipe.show_progress(total)

        def update_progress(done: int) -> None:
            if sink is not None:
                sink.update_progress(done)
            elif pipe is not None:
                pipe.update_progress(done)

        def report_bytes(n: int) -> None:
            if sink is not None:
                sink.add_bytes(n)
            elif pipe is not None:
                pipe.add_bytes(n)

        def report_activity(text: str) -> None:
            if sink is not None:
                sink.set_activity(text)
            elif pipe is not None:
                pipe.set_activity(text)

        async def clear_progress() -> None:
            if sink is not None:
                sink.clear_progress()
            elif pipe is not None:
                pipe.clear_progress()

        async def ok_message(message: str) -> None:
            if sink is not None:
                await sink.succeed(message)
            elif pipe is not None:
                await pipe.succeed(message)

        async def bad_message(message: str) -> None:
            if sink is not None:
                await sink.fail(message)
            elif pipe is not None:
                await pipe.fail(message)

        async with (pipe if pipe is not None else contextlib.nullcontext()):
            if not self._images and self._images_iter is None:
                await bad_message(f"{series_prefix}No downloadable images found")
                return PipelineResult(ok=False, error="no downloadable images found")

            await stage(f"{series_prefix}Downloading images...")
            await show_progress(total=self._page_total())

            client_kwargs = http_client_args(referer_url=self._referer_url)

            self._bytes_cb = report_bytes
            try:
                failed = await self._download(
                    client_kwargs, update_progress, activity_cb=report_activity
                )
            finally:
                self._bytes_cb = None
            if not self._quiet and failed:
                print_error(
                    f"{series_prefix}{len(failed)} pages failed to download"
                )
            images = self._current_images()

            await clear_progress()
            await stage(f"{series_prefix}Verifying images...")
            self._tmp_dir.mkdir(parents=True, exist_ok=True)
            verify_errors, verified_formats = verify_downloads(
                images, self._tmp_dir, known_formats=self._stream_formats
            )
            if verify_errors:
                for fname, reason in verify_errors.items():
                    # A download that failed already carries its own label
                    # (HTTP status, timeout, ...); the verify pass on the same
                    # file can only add a redundant "missing".
                    self._failure_labels.setdefault(
                        fname, f"verification: {reason}"
                    )
                # Report only files that reached disk but failed verification;
                # download failures were already counted and answered above.
                verify_only = {
                    n: r for n, r in verify_errors.items() if n not in failed
                }
                names = list(verify_only.keys())
                if not self._quiet and names:
                    if len(names) <= 5:
                        for fname, reason in verify_only.items():
                            print_error(f"{series_prefix}{fname}: {reason}")
                    else:
                        first_five = [f"{n}: {verify_only[n]}" for n in names[:5]]
                        print_error_block(
                            f"{series_prefix}{len(names)} pages failed "
                            "verification (showing first 5):",
                            first_five,
                        )
                failed.update(verify_errors.keys())

            if self._failure_labels and VERBOSITY >= DIAGNOSTIC:
                # One attributable tally — "HTTP 530 x34" — instead of a wall
                # of per-page lines at this level.
                from collections import Counter

                tally = Counter(self._failure_labels.values())
                vlog(
                    DIAGNOSTIC,
                    "page failures: "
                    + ", ".join(
                        f"{label} x{n}" for label, n in sorted(tally.items())
                    ),
                    tag=TAG_DOWNLOAD,
                )

            if not verified_formats:
                # Zero valid pages: fail the chapter outright instead of
                # writing an archive that contains only ComicInfo.xml. The
                # existing CBZ (if any) is left untouched.
                await bad_message(
                    f"{series_prefix}No valid pages downloaded ({len(failed)} failed)."
                )
                shutil.rmtree(self._tmp_dir, ignore_errors=True)
                return PipelineResult(
                    ok=False,
                    error="no valid pages downloaded",
                    failed_images=failed,
                    failed_reasons=dict(self._failure_labels),
                )

            await stage(f"{series_prefix}Creating CBZ archive...")
            pack_state: dict[str, int] = {"n": 0, "total": len(images)}

            def _on_packed(n: int, total: int) -> None:
                pack_state["n"] = n
                pack_state["total"] = total

            try:
                pack_task = asyncio.create_task(
                    asyncio.to_thread(
                        create_archive,
                        images=images,
                        source_dir=self._tmp_dir,
                        output_path=self._cbz_path,
                        series_title=self._series_title,
                        chapter_title=self._chapter_title,
                        source_url=self._url,
                        verified_formats=verified_formats,
                        chapter_number=self._chapter_number,
                        volume_number=self._volume_number,
                        series_meta=self._series_meta,
                        compression=self._compression,
                        on_packed=_on_packed,
                    )
                )
                announce = asyncio.create_task(
                    _announce_pack(series_prefix, pack_state, stage, pack_task)
                )
                try:
                    added, _ = await pack_task
                finally:
                    announce.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await announce
            except ValueError as e:
                await bad_message(f"Archive error: {e}")
                return PipelineResult(
                    ok=False, error=f"Archive error: {e}",
                    failed_images=failed,
                    failed_reasons=dict(self._failure_labels),
                )

            cbz_size = 0
            suffix = ""
            try:
                cbz_size = self._cbz_path.stat().st_size
                suffix = (
                    f" ({cbz_size / 1024 / 1024:.1f} MB)"
                    if cbz_size > 1024 * 1024
                    else f" ({cbz_size / 1024:.0f} KB)"
                )
            except OSError:
                pass

            label = series_prefix or ""
            await ok_message(f"{label}Saved: {self._cbz_path.name}{suffix}")

            return PipelineResult(
                ok=True,
                cbz_path=self._cbz_path,
                cbz_size=cbz_size,
                cbz_pages=added,
                failed_images=failed,
                failed_reasons=dict(self._failure_labels),
            )

    # ── internal: download step ─────────────────────────────────

    async def _download(
        self,
        client_kwargs: dict,
        progress_cb: Callable[[int], None],
        activity_cb: Callable[[str], None] | None = None,
    ) -> set[str]:
        bytes_cb = self._bytes_cb
        stream_formats: dict[str, str] = {}
        self._failure_labels = {}
        if self._images_iter is not None:
            failed = await self._download_stream(
                client_kwargs, progress_cb, bytes_cb, activity_cb,
                stream_formats=stream_formats,
            )
        elif self._client is not None:
            failed = await download_httpx(
                self._images, self._tmp_dir, self._concurrency,
                progress_cb, client=self._client, max_image_size=self._max_image_size,
                max_total_size=self._max_total_size, bytes_cb=bytes_cb,
                activity_cb=activity_cb, stream_formats=stream_formats,
                download_timeout=self._download_timeout,
                max_attempts=self._max_attempts,
                failure_labels=self._failure_labels,
            )
        else:
            session_kwargs = {**client_kwargs, "max_clients": self._concurrency * 2}
            async with AsyncSession(**session_kwargs) as c:
                failed = await download_httpx(
                    self._images, self._tmp_dir, self._concurrency,
                    progress_cb, client=c, max_image_size=self._max_image_size,
                    max_total_size=self._max_total_size, bytes_cb=bytes_cb,
                    activity_cb=activity_cb, stream_formats=stream_formats,
                    download_timeout=self._download_timeout,
                    max_attempts=self._max_attempts,
                    failure_labels=self._failure_labels,
                )
        self._stream_formats = stream_formats
        return failed

    async def _download_stream(
        self,
        client_kwargs: dict,
        progress_cb: Callable[[int], None],
        bytes_cb: Callable[[int], None] | None = None,
        activity_cb: Callable[[str], None] | None = None,
        stream_formats: dict[str, str] | None = None,
    ) -> set[str]:
        if self._images_iter is None:
            raise RuntimeError("_download_stream called without an images iterator")
        if self._client is not None:
            failed, resolved = await download_httpx_iter(
                self._images_iter, self._tmp_dir, self._concurrency,
                progress_cb, client=self._client, max_image_size=self._max_image_size,
                max_total_size=self._max_total_size, bytes_cb=bytes_cb,
                activity_cb=activity_cb, stream_formats=stream_formats,
                download_timeout=self._download_timeout,
                max_attempts=self._max_attempts,
                failure_labels=self._failure_labels,
            )
        else:
            session_kwargs = {**client_kwargs, "max_clients": self._concurrency * 2}
            async with AsyncSession(**session_kwargs) as c:
                failed, resolved = await download_httpx_iter(
                    self._images_iter, self._tmp_dir, self._concurrency,
                    progress_cb, client=c, max_image_size=self._max_image_size,
                    max_total_size=self._max_total_size, bytes_cb=bytes_cb,
                    activity_cb=activity_cb, stream_formats=stream_formats,
                    download_timeout=self._download_timeout,
                    max_attempts=self._max_attempts,
                    failure_labels=self._failure_labels,
                )
        self._resolved_images = resolved
        return failed
