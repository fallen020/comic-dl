"""Shared scraper helpers, the BaseScraper base class, and safe page fetching."""

from __future__ import annotations

import asyncio
import html
import json
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from ..cf import retry_challenge_once
from ..errors import (
    SITE_NO_CHAPTERS,
    SITE_NO_PAGES,
    SITE_NOT_RECOGNIZED,
    ScrapeError,
    ScrapeTimeout,
)
from ..http import absorb_response_cookies, jar_cookies_kwargs
from ..rate import await_ratelimit
from ..ui import DIAGNOSTIC, http_event
from ..utils import (
    MAX_REDIRECTS,
    RequestBlockedError,
    aclose_response,
    resolve_redirect_url_async,
    validate_request_url_async,
)

_VALID_IMAGE_EXTS = frozenset({"jpg", "jpeg", "png", "webp", "gif", "bmp"})

SCRAPE_TIMEOUT = 30.0

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def no_images_error(hint: str = "", code: str | None = None) -> ScrapeError:
    """The standard "page loaded but yielded no images" failure."""
    default = "The page may require login, be region-locked, or have been removed."
    return ScrapeError(
        "No images found on this page.",
        hint=hint or default,
        site_error_code=code or SITE_NO_PAGES,
    )


def no_chapters_error(hint: str = "", code: str | None = None) -> ScrapeError:
    """The standard "series page yielded no chapters" failure."""
    default = "The series may be empty, or its page layout changed."
    return ScrapeError(
        "No chapters found on series page.",
        hint=hint or default,
        site_error_code=code or SITE_NO_CHAPTERS,
    )


def listing_page_error(site_name: str, example_url: str) -> ScrapeError:
    """A category/tag/archive URL was handed to a chapter/series scrape."""
    return ScrapeError(
        "This is a category/tag listing page, not a comic.",
        hint=f"{site_name} requires a comic page URL like {example_url}",
        site_error_code=SITE_NOT_RECOGNIZED,
    )


_T = TypeVar("_T")


async def retry_transient(
    op: Callable[[int], Awaitable[_T]],
    *,
    tries: int,
    delay: Callable[[int, BaseException], float],
    is_transient: Callable[[BaseException], bool],
) -> _T:
    """Run ``op(attempt)`` up to ``tries`` times, backing off between attempts.

    Only errors where ``is_transient`` holds are retried, after
    ``delay(attempt, exc)`` seconds; anything else propagates immediately.
    When every attempt fails transiently, the last error is re-raised for
    the caller to wrap (or swallow, for best-effort paths).
    """
    last_exc: BaseException | None = None
    for attempt in range(max(1, tries)):
        try:
            return await op(attempt)
        except Exception as exc:
            if not is_transient(exc):
                raise
            last_exc = exc
            if attempt < tries - 1:
                await asyncio.sleep(delay(attempt, exc))
    if last_exc is None:  # tries < 1, so op never ran
        raise ValueError("retry_transient needs tries >= 1")
    raise last_exc


_JSONLD_SEL = 'script[type="application/ld+json"]'


def _looks_like_json(body: bytes) -> bool:
    """True when ``body`` parses as JSON (API-response shape guard)."""
    try:
        json.loads(body)
    except ValueError:
        return False
    return True


JSONLD_ARTICLE_TYPES = frozenset({"Article", "NewsArticle", "BlogPosting"})


def extract_jsonld(soup: BeautifulSoup) -> list[dict]:
    """Flatten every JSON-LD node (following ``@graph``) into one list.

    Non-dict nodes and malformed scripts are skipped; nested ``@graph``
    lists are inlined so callers can filter on ``@type`` without walking
    the graph themselves.
    """
    nodes: list[dict] = []
    for script in soup.select(_JSONLD_SEL):
        if not script.string:
            continue
        try:
            data = json.loads(html.unescape(script.string))
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@graph") and isinstance(item["@graph"], list):
                nodes.extend(n for n in item["@graph"] if isinstance(n, dict))
            else:
                nodes.append(item)
    return nodes


def jsonld_type_includes(node: dict, wanted: str) -> bool:
    """True when a node's ``@type`` (str or list) includes ``wanted``."""
    raw = node.get("@type")
    types = raw if isinstance(raw, list) else [raw]
    return any(isinstance(t, str) and t == wanted for t in types)


def article_jsonld_nodes(soup: BeautifulSoup) -> list[dict]:
    """JSON-LD nodes whose ``@type`` includes an article type."""
    return [
        n
        for n in extract_jsonld(soup)
        if any(jsonld_type_includes(n, t) for t in JSONLD_ARTICLE_TYPES)
    ]


def _attr_text(value: object) -> str:
    """Coerce a bs4 tag attribute to a trimmed ``str``.

    ``Tag.get()`` is typed as ``str | list[str] | None``; this collapses that
    to a single string so downstream code can rely on ``str`` methods.
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and value and isinstance(value[0], str):
        return value[0].strip()
    return ""


def meta_index(soup: BeautifulSoup) -> dict[str, list[str]]:
    """One find_all("meta") pass -> {attr:value: [contents]} (document order).

    Values are keyed by both the ``property`` and ``name`` attributes, lowercased,
    and stored as lists so multi-valued keys (e.g. ``article:tag``) keep their order.
    """
    idx: dict[str, list[str]] = {}
    for tag in soup.find_all("meta"):
        content = _attr_text(tag.get("content"))
        if not content:
            continue
        prop = _attr_text(tag.get("property")).lower()
        name = _attr_text(tag.get("name")).lower()
        if prop:
            idx.setdefault(f"prop:{prop}", []).append(content)
        if name:
            idx.setdefault(f"name:{name}", []).append(content)
    return idx


def meta_get(idx: dict[str, list[str]], *names: str) -> str:
    """Return the first content matching any of ``names`` (property or name attr)."""
    for n in names:
        key = n.lower()
        vals = idx.get(f"prop:{key}")
        if vals:
            return vals[0]
        vals = idx.get(f"name:{key}")
        if vals:
            return vals[0]
    return ""


class SeriesDataCache:
    """Per-scraper enrichment cache with single-flight loading.

    ``get`` returns the cached entry when present; otherwise exactly one
    caller runs ``loader()`` while concurrent same-key callers await its
    result, so N chapters racing one slug fetch the series page once
    instead of N times. Loader failures degrade to (and cache) an empty
    dict, so a dead series page is fetched once per key, not once per
    chapter. Locks are per key and intentionally never removed: keys are
    bounded by series seen per run, and removal would race late arrivals
    against in-flight waiters.
    """

    def __init__(self) -> None:
        self._data: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def __contains__(self, key: object) -> bool:
        return key in self._data

    async def get(self, key: str, loader: Callable[[], Awaitable[dict]]) -> dict:
        hit = self._data.get(key)
        if hit is not None:
            return hit
        async with self._locks.setdefault(key, asyncio.Lock()):
            hit = self._data.get(key)
            if hit is not None:
                return hit
            try:
                data = await loader()
            # Enrichment is best-effort.
            except Exception:  # nosec
                data = {}
            self._data[key] = data
            return data


class BaseScraper:
    """Shared HTTP, retry, and metadata helpers for every built-in scraper."""

    domain: str = ""

    @staticmethod
    async def fetch_html(url: str, client: AsyncSession) -> BeautifulSoup:
        soup, _ = await BaseScraper.fetch_html_raw(url, client)
        return soup

    @staticmethod
    async def fetch_html_raw(url: str, client: AsyncSession) -> tuple[BeautifulSoup, str]:
        resp = await BaseScraper._timeout_get(url, client)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml"), resp.text

    @staticmethod
    async def _timeout_get(
        url: str,
        client: AsyncSession,
        method: str = "GET",
        rate: float | None = None,
        json: object = None,
        headers: dict[str, str] | None = None,
        use_cache: bool = True,
        challenge_retry: bool = True,
        expect_json: bool = False,
    ):
        """Validate + fetch ``url`` bounded by a hard timeout, validating hops.

        GET ``url`` (or ``HEAD``/``POST`` when ``method`` differs) under a hard
        timeout. Without the explicit ``asyncio.timeout``, curl_cffi's own
        session timeout can be ignored by a stalled/trickling response —
        leaving the caller hanging indefinitely (and, if the loop wedges,
        uninterruptible).

        ``rate`` overrides the host's rate-limit for this single request so
        callers can run cheap page views faster than the host default.

        ``json`` sends a JSON request body (e.g. POST-only APIs); ``headers``
        merges extra request headers on top of the session defaults.
        ``challenge_retry`` runs the Cloudflare detect-solve-retry ladder;
        pass False for a single attempt when the caller escalates itself
        (e.g. a scraper that prefers its stored cookie and only opens a
        webview when the replay is actually challenged).
        ``expect_json`` guards API callers against a poisoned entry: a
        cached body that is not JSON is dropped and refetched, and a
        non-JSON network body is never stored (a transient 200 HTML shell
        must not shadow the API for the rest of the TTL).

        Scrape-path fetches must also satisfy the same outbound-safety
        invariant the downloader enforces on image/cover fetches: the initial
        URL and each redirect ``Location`` are checked by
        :func:`validate_request_url`, so a page that is public today can never
        302 onto a loopback/private/metadata address. Redirects are followed
        manually (automatic following would skip the per-hop checks) and capped
        at ``MAX_REDIRECTS``.

        Metadata GETs are cached on disk (:mod:`comic_dl.cache`): a fresh
        entry is served without network I/O; a stale entry triggers one
        conditional request (``If-None-Match``/``If-Modified-Since``) whose
        ``304`` refreshes the entry for another TTL. The cache is consulted
        only after ``url`` has been validated, and is bypassed when
        ``--no-cache``/``--no-cookie`` changes the run's cookie semantics.
        """
        current = await validate_request_url_async(url)
        req = getattr(client, method.lower())

        from ..cache import (
            CachedResponse,
            cache_enabled,
            conditional_headers,
        )
        from ..cache import (
            lookup as cache_lookup,
        )
        from ..cache import (
            refresh as cache_refresh,
        )
        from ..cache import (
            store as cache_store,
        )
        from ..config import http_setting
        from ..http import cookie_jar_enabled

        cacheable = (
            use_cache
            and method == "GET"
            and json is None
            and cache_enabled()
            and cookie_jar_enabled()
        )
        profile = http_setting("impersonate", "chrome146") or ""
        extra = dict(headers or {})
        stale_entry = None
        if cacheable:
            cached, stale_entry = cache_lookup(url, profile, extra, method=method)
            if cached is not None:
                if expect_json and not _looks_like_json(bytes(cached.content or b"")):
                    from ..cache import invalidate as cache_invalidate

                    cache_invalidate(url, profile, extra)
                else:
                    return cached

        if stale_entry is not None:
            merged = dict(headers or {})
            merged.update(conditional_headers(stale_entry))
            headers = merged

        async def _fetch_once():
            nonlocal current
            resp = None
            for _ in range(MAX_REDIRECTS + 1):
                await await_ratelimit(urlsplit(current).hostname or current, rate=rate)
                try:
                    _started = time.monotonic()
                    async with asyncio.timeout(SCRAPE_TIMEOUT):
                        kwargs: dict = {**jar_cookies_kwargs(current)}
                        if json is not None:
                            kwargs["json"] = json
                        if headers:
                            kwargs["headers"] = headers
                        resp = await req(
                            current,
                            allow_redirects=False,
                            **kwargs,
                        )
                    _elapsed = time.monotonic() - _started
                except TimeoutError as e:
                    raise ScrapeTimeout(url, SCRAPE_TIMEOUT) from e
                absorb_response_cookies(client, getattr(resp, "headers", None))
                status = resp.status_code
                http_event(
                    method,
                    current,
                    status=status,
                    duration=_elapsed,
                    headers=getattr(resp, "headers", None) or {},
                    level=DIAGNOSTIC,
                )
                location = (getattr(resp, "headers", None) or {}).get("location")
                if status in _REDIRECT_STATUSES and location:
                    await aclose_response(resp)
                    current = await resolve_redirect_url_async(current, location)
                    continue
                return resp
            if resp is not None:
                await aclose_response(resp)
            raise RequestBlockedError(
                f"too many redirects ({MAX_REDIRECTS}) while following {url!r}"
            )

        if challenge_retry:
            resp = await retry_challenge_once(_fetch_once, url)
        else:
            resp = await _fetch_once()
        if cacheable and stale_entry is not None and resp.status_code == 304:
            cache_refresh(url, profile, extra, stale_entry, method=method)
            return CachedResponse(stale_entry)
        body = bytes(getattr(resp, "content", b"") or b"")
        if (
            cacheable
            and resp.status_code == 200
            and not (expect_json and not _looks_like_json(body))
        ):
            cache_store(
                url,
                profile,
                extra,
                method=method,
                status=resp.status_code,
                headers=dict(getattr(resp, "headers", None) or {}),
                body=body,
            )
        return resp

    @staticmethod
    def meta(soup: BeautifulSoup, *names: str) -> str:
        return meta_get(meta_index(soup), *names)

    @staticmethod
    def clean_image_url(raw: str) -> str:
        return raw.split("?")[0].split("#")[0]

    @staticmethod
    def image_ext(url: str) -> str:
        path = url.split("?")[0]
        try:
            _, ext = path.rsplit(".", 1)
        except ValueError:
            return "jpg"
        ext = ext.lower()
        return ext if ext in _VALID_IMAGE_EXTS else "jpg"
