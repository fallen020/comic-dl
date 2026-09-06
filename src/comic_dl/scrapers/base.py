"""Shared scraper helpers, the BaseScraper base class, and safe page fetching."""

from __future__ import annotations

import asyncio
import html
import inspect
import json
import time

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from ..cf import retry_challenge_once
from ..errors import ScrapeError, ScrapeTimeout
from ..http import absorb_response_cookies, jar_cookies_kwargs
from ..rate import await_ratelimit
from ..ui import DIAGNOSTIC, http_event
from ..utils import (
    MAX_REDIRECTS,
    RequestBlockedError,
    resolve_redirect_url_async,
    validate_request_url_async,
)

_VALID_IMAGE_EXTS = frozenset({"jpg", "jpeg", "png", "webp", "gif", "bmp"})

SCRAPE_TIMEOUT = 30.0

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def no_images_error(hint: str = "") -> ScrapeError:
    """The standard "page loaded but yielded no images" failure."""
    default = (
        "The page may require login, be region-locked, or have been removed."
    )
    return ScrapeError("No images found on this page.", hint=hint or default)


def no_chapters_error() -> ScrapeError:
    """The standard "series page yielded no chapters" failure."""
    return ScrapeError(
        "No chapters found on series page.",
        hint="The series may be empty, or its page layout changed.",
    )


def listing_page_error(site_name: str, example_url: str) -> ScrapeError:
    """A category/tag/archive URL was handed to a chapter/series scrape."""
    return ScrapeError(
        "This is a category/tag listing page, not a comic.",
        hint=f"{site_name} requires a comic page URL like {example_url}",
    )


_JSONLD_SEL = 'script[type="application/ld+json"]'

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
        n for n in extract_jsonld(soup)
        if any(jsonld_type_includes(n, t) for t in JSONLD_ARTICLE_TYPES)
    ]


async def _close_response(resp: object) -> None:
    closer = getattr(resp, "aclose", None) or getattr(resp, "close", None)
    if closer is None:
        return
    try:
        result = closer()
        if inspect.isawaitable(result):
            await result
    except Exception:  # nosec B110
        pass


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
                return cached

        if stale_entry is not None:
            merged = dict(headers or {})
            merged.update(conditional_headers(stale_entry))
            headers = merged

        async def _fetch_once():
            nonlocal current
            resp = None
            for _ in range(MAX_REDIRECTS + 1):
                await await_ratelimit(current, rate=rate)
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
                    await _close_response(resp)
                    current = await resolve_redirect_url_async(current, location)
                    continue
                return resp
            if resp is not None:
                await _close_response(resp)
            raise RequestBlockedError(
                f"too many redirects ({MAX_REDIRECTS}) while following {url!r}"
            )

        resp = await retry_challenge_once(_fetch_once, url)
        if cacheable and stale_entry is not None and resp.status_code == 304:
            cache_refresh(url, profile, extra, stale_entry, method=method)
            return CachedResponse(stale_entry)
        if cacheable and resp.status_code == 200:
            cache_store(
                url,
                profile,
                extra,
                method=method,
                status=resp.status_code,
                headers=dict(getattr(resp, "headers", None) or {}),
                body=bytes(getattr(resp, "content", b"") or b""),
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
