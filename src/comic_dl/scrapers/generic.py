"""Generic fallback scraper modeled on yt-dlp's ``GenericIE``.

A last-resort scraper for URLs no registered source claims: a direct image
URL, an image gallery page, or a chapter-list / series page. It extracts only
from static HTML and embedded JSON (no JS execution, no webview), and only
ever runs after every domain-keyed source lookup has returned ``None`` — it
never shadows a site-specific scraper.

The CLI gates it behind ``--no-generic`` / ``[download] generic`` (on by
default) and emits a visible note when it fires.
"""

from __future__ import annotations

import contextlib
import html
import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from ..models import ImageItem, PostMetadata, SeriesMetadata
from ..utils import (
    RequestBlockedError,
    image_source_name,
    normalize_url,
    normalize_url_key,
    validate_request_url,
)
from .base import (
    BaseScraper,
    _attr_text,
    meta_get,
    meta_index,
    no_chapters_error,
    no_images_error,
)
from .registry import register_generic

# Image extensions a direct-link URL may carry. ``avif`` is covered by the
# downloader's magic-byte map (see ``comic_dl.utils.IMAGE_MAGIC``).
DIRECT_IMAGE_EXTS = frozenset({
    "jpg", "jpeg", "png", "webp", "gif", "bmp", "avif",
})

# Non-image asset extensions: a link/image candidate ending in one of these is
# never a gallery page image.
_ASSET_EXTS = DIRECT_IMAGE_EXTS | frozenset({
    "svg", "css", "js", "mjs", "ico", "woff", "woff2", "ttf", "eot",
    "mp4", "webm", "mp3", "ogg", "zip", "pdf", "html", "htm", "xml", "json",
})

# Series detection threshold (Phase-0 decision #2): a page is a series/chapter
# list when it carries at least this many deep internal links and fewer images
# than links.
SERIES_MIN_LINKS = 3

# URL fragments that mark an image candidate as decorative/placeholder rather
# than a gallery page. A naive ``src``-only read otherwise harvests hundreds of
# identical 1 KB transparent squares.
_PLACEHOLDER_KEYWORDS = (
    "placeholder", "spacer", "loader", "loading", "icon", "logo", "avatar",
    "favicon", "sprite", "pixel", "blank", "preview", "transparent", "spinner",
)

# Lazy-loading attributes that hide the real image URL from ``src``.
_LAZY_ATTRS = ("data-src", "data-original", "data-lazy-src", "data-image")

# JSON keys whose string value may hold an image URL (JSON-LD / __NEXT_DATA__).
_IMAGE_KEYS = frozenset({
    "image", "images", "src", "url", "contenturl", "content_url",
})

_BACKGROUND_RE = re.compile(
    r"background(?:-image)?\s*:\s*url\(\s*['\"]?([^'\")]+)['\"]?\s*\)",
    re.IGNORECASE,
)

_TITLE_SEPS = (" - ", " | ", " \u2013 ", " \u2014 ", " :: ", "\u00b7")


def _host_of(url: str) -> str:
    return urlparse(url).hostname or ""


def _path_segment(url: str) -> str:
    """Last path segment of ``url``, extension stripped and de-kebabbed."""
    parsed = urlparse(url)
    segments = [p for p in parsed.path.split("/") if p]
    if not segments:
        return ""
    last = segments[-1]
    stem = last.rsplit(".", 1)[0] if "." in last else last
    cleaned = stem.replace("-", " ").replace("_", " ").strip()
    return cleaned or stem


def _path_is_asset(path: str) -> bool:
    """True when ``path``'s final segment ends in a known asset extension."""
    last = path.rstrip("/").rsplit("/", 1)[-1]
    if "." not in last:
        return False
    return last.rsplit(".", 1)[-1].lower() in _ASSET_EXTS


def _is_image_url(url: str) -> bool:
    """True when ``url``'s path ends in an image extension."""
    last = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    if "." not in last:
        return False
    return last.rsplit(".", 1)[-1].lower() in DIRECT_IMAGE_EXTS


def _looks_like_direct_image(url: str) -> bool:
    """Cheap path-extension test for a direct image URL (no fetch)."""
    return _is_image_url(url)


def _is_placeholder(url: str) -> bool:
    lowered = url.lower()
    return any(keyword in lowered for keyword in _PLACEHOLDER_KEYWORDS)


def _largest_srcset(srcset: str) -> str:
    """Pick the widest (or densest) candidate from a ``srcset`` string.

    Browsers choose by viewport; statically the widest ``w`` descriptor is the
    best approximation of the full-resolution image, falling back to the
    highest ``x`` density. Data URIs (blur-up placeholders) are skipped.
    """
    best: str | None = None
    best_key = (-1, -1)
    for part in srcset.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split()
        url = bits[0]
        if not url or url.lower().startswith("data:"):
            continue
        width = -1
        density = 1.0
        for bit in bits[1:]:
            if bit.endswith("w") and bit[:-1].isdigit():
                width = int(bit[:-1])
            elif bit.endswith("x"):
                with contextlib.suppress(ValueError):
                    density = float(bit[:-1])
        key = (width, int(density * 1000))
        if key > best_key:
            best_key = key
            best = url
    return best or ""


def _candidate_urls(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Ordered, deduped image-candidate URLs from every static source.

    Tier 1 (primary ordering): ``<img>`` ``src`` → lazy attrs → largest
    ``srcset``, plus ``<picture><source>`` and ``<noscript><img>`` fallbacks, in
    document order. Tier 2: CSS ``background-image`` in inline styles and
    ``<style>`` blocks (document order of owning elements). Candidates are
    resolved against ``base_url``, deduped (fragment stripped), filtered for
    placeholder keywords / non-image assets, and run through
    :func:`validate_request_url`.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        if not raw:
            return
        lowered = raw.lstrip().lower()
        if lowered.startswith(("data:", "about:", "blob:", "javascript:", "mailto:")):
            return
        resolved = urljoin(base_url, raw)
        if _path_is_asset(urlparse(resolved).path) and not _is_image_url(resolved):
            return
        if _is_placeholder(resolved):
            return
        key = resolved.split("#")[0]
        if key in seen:
            return
        seen.add(key)
        try:
            validate_request_url(resolved)
        except RequestBlockedError:
            return
        candidates.append(resolved)

    for img in soup.find_all("img"):
        if img.find_parent("picture") is not None:
            continue
        src = _attr_text(img.get("src"))
        if src:
            add(src)
        for attr in _LAZY_ATTRS:
            lazy = _attr_text(img.get(attr))
            if lazy:
                add(lazy)
        for attr in ("srcset", "data-srcset"):
            srcset = _attr_text(img.get(attr))
            if srcset:
                add(_largest_srcset(srcset))

    for picture in soup.find_all("picture"):
        # Prefer the first <source> (browsers pick the first matching media
        # query); statically the largest candidate of the first source is the
        # best approximation of the displayed image.
        value = ""
        for source in picture.find_all("source"):
            candidate = _attr_text(source.get("srcset")) or _attr_text(source.get("src"))
            if candidate:
                value = candidate
                break
        if not value:
            pimg = picture.find("img")
            if pimg is not None:
                value = _attr_text(pimg.get("src"))
                if not value:
                    for attr in _LAZY_ATTRS:
                        value = _attr_text(pimg.get(attr))
                        if value:
                            break
        if value:
            add(_largest_srcset(value))

    for noscript in soup.find_all("noscript"):
        for img in noscript.find_all("img"):
            src = _attr_text(img.get("src"))
            if src:
                add(src)

    for el in soup.find_all(style=True):
        style = _attr_text(el.get("style"))
        if style:
            for match in _BACKGROUND_RE.finditer(style):
                add(match.group(1))

    for style_tag in soup.find_all("style"):
        text = style_tag.get_text()
        if text:
            for match in _BACKGROUND_RE.finditer(text):
                add(match.group(1))

    return candidates


def _walk_images(node: object) -> list[str]:
    """Collect image-like string URLs from parsed JSON.

    Recurses through dicts/lists and keeps string values living under image-ish
    keys (``image``/``images``/``src``/``url``/``contentUrl``). Covers a bare
    string image, a list of strings/objects under one image-ish key, and a
    nested ``ImageObject`` whose inner ``url`` sits under one of those keys.
    """
    out: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key.lower() in _IMAGE_KEYS:
                if isinstance(value, str):
                    if _is_image_url(value):
                        out.append(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            if _is_image_url(item):
                                out.append(item)
                        else:
                            out.extend(_walk_images(item))
                else:
                    out.extend(_walk_images(value))
            elif isinstance(value, (dict, list)):
                out.extend(_walk_images(value))
    elif isinstance(node, list):
        for item in node:
            out.extend(_walk_images(item))
    return out


def _structured_image_urls(soup: BeautifulSoup, base_url: str) -> list[str]:
    """JSON-LD / ``__NEXT_DATA__`` image URLs (last-resort tier).

    Only consulted when no ``<img>``-derived candidates exist, so embedded-JSON
    images can never override document order. Results are deduped
    (fragment-stripped) in first-seen order — the same URL routinely appears
    in both JSON-LD and ``__NEXT_DATA__``, and the downloader has no URL
    dedup of its own.
    """
    urls: list[str] = []
    seen: set[str] = set()

    def collect(value: str) -> None:
        resolved = urljoin(base_url, value)
        if not _is_image_url(resolved) or _is_placeholder(resolved):
            return
        key = resolved.split("#")[0]
        if key in seen:
            return
        seen.add(key)
        urls.append(resolved)

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(html.unescape(script.string or script.get_text()))
        except (json.JSONDecodeError, ValueError):
            continue
        for value in _walk_images(data):
            collect(value)

    next_data = soup.select_one('script#__NEXT_DATA__')
    if next_data is not None:
        try:
            data = json.loads(html.unescape(next_data.string or next_data.get_text()))
        except (json.JSONDecodeError, ValueError):
            data = None
        if isinstance(data, dict):
            for value in _walk_images(data):
                collect(value)
    return urls


def _extract_gallery_images(soup: BeautifulSoup, base_url: str) -> list[ImageItem]:
    """Ordered, numbered ``ImageItem`` list from a gallery page's soup."""
    urls = _candidate_urls(soup, base_url)
    if not urls:
        urls = _structured_image_urls(soup, base_url)
    images: list[ImageItem] = []
    for index, url in enumerate(urls):
        item = ImageItem.from_url(url, page_number=index + 1)
        if item is None:
            item = ImageItem(
                url=url,
                page_number=index + 1,
                filename=f"page_{index + 1:04d}.jpg",
            )
        images.append(item)
    return images


def _deep_links(soup: BeautifulSoup, base_url: str) -> list[tuple[str, str]]:
    """Internal deep non-asset links in document order.

    Returns ``(absolute_url, link_text)`` pairs for anchors whose hrefs are
    deep paths (≥2 segments, not asset files) on the same registrable domain,
    skipping bare fragments, ``mailto:``/``javascript:`` links, and links to
    the page itself. A series page's chapter links (``/manga/foo/chapter-1``)
    match; nav chrome (prev/next/first/last) generally does not because it is
    too shallow or targets the same path.
    """
    parsed_base = urlparse(base_url)
    base_host = (parsed_base.hostname or "").lower()
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = _attr_text(anchor.get("href"))
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        resolved = urljoin(base_url, href)
        parsed = urlparse(resolved)
        host = (parsed.hostname or "").lower()
        if host != base_host and not host.endswith("." + base_host):
            continue
        path = parsed.path or "/"
        if path == parsed_base.path or path in ("", "/"):
            continue
        if len([p for p in path.split("/") if p]) < 2:
            continue
        if _path_is_asset(path):
            continue
        key = normalize_url_key(resolved)
        if key in seen:
            continue
        seen.add(key)
        text = anchor.get_text(" ", strip=True) or ""
        links.append((resolved, text))
    return links


def _episode_no(text: str, fallback: int) -> str:
    """Trailing number from a chapter title/href, else ``fallback``."""
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    return match.group(1) if match else str(fallback)


def _json_ld_name(soup: BeautifulSoup) -> str:
    """First top-level JSON-LD ``name``, if any."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(html.unescape(script.string or script.get_text()))
        except (json.JSONDecodeError, ValueError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict):
                name = item.get("name")
                if isinstance(name, str) and name.strip():
                    return html.unescape(name.strip())
    return ""


def _page_title(soup: BeautifulSoup, idx: dict[str, list[str]], url: str) -> str:
    """Title fallback chain: ``<title>`` → OG → JSON-LD → path segment."""
    title_tag = soup.select_one("title")
    if title_tag is not None:
        text = title_tag.get_text(" ", strip=True)
        if text:
            return text
    og = meta_get(idx, "og:title", "twitter:title")
    if og:
        return og
    ld = _json_ld_name(soup)
    if ld:
        return ld
    return _path_segment(url) or _host_of(url)


def _series_title_for(url: str, title: str) -> str:
    """Series title from a page title: first segment before a title separator."""
    if not title:
        return _path_segment(url) or _host_of(url)
    for sep in _TITLE_SEPS:
        if sep in title:
            head = title.split(sep, 1)[0].strip()
            if head:
                return head
    return title


def _cover_url(idx: dict[str, list[str]], base_url: str) -> str:
    raw = meta_get(idx, "og:image", "twitter:image")
    if not raw:
        return ""
    first = raw.split(",")[0].strip()
    return urljoin(base_url, first).split("#")[0]


def _description(idx: dict[str, list[str]]) -> str:
    return meta_get(idx, "og:description", "twitter:description", "description")


class GenericScraper(BaseScraper):
    """Last-resort static HTML/JSON scraper for unregistered hosts.

    Mirrors yt-dlp's ``GenericIE``: probe the cheap static signals first
    (direct image extension / ``Content-Type``, then structured data), report
    which path fired, and never let generic shadow a site-specific source.

    Fetching is shared across calls: the page soup is cached per URL on the
    instance, so the CLI's classify-then-scrape flow and each chapter hop of a
    generic series pay for a single page fetch each.
    """

    domain = ""
    name = "generic"
    version = "builtin"

    def __init__(self) -> None:
        super().__init__()
        self._page_cache: dict[str, BeautifulSoup] = {}
        self._direct_urls: set[str] = set()

    def matches_url(self, url: str) -> bool:
        return url.lower().startswith(("http://", "https://"))

    def _cache_key(self, url: str) -> str:
        return normalize_url_key(url)

    async def _load_page(self, url: str, client: AsyncSession) -> BeautifulSoup:
        key = self._cache_key(url)
        cached = self._page_cache.get(key)
        if cached is not None:
            return cached
        soup, _ = await self.fetch_html_raw(url, client)
        self._page_cache[key] = soup
        return soup

    async def _resolve_page(
        self, url: str, client: AsyncSession,
    ) -> BeautifulSoup | None:
        """Fetch/classify ``url``; return soup for HTML pages or ``None`` for a
        direct image URL (remembered so later calls skip re-probing)."""
        key = self._cache_key(url)
        if key in self._page_cache:
            return self._page_cache[key]
        if key in self._direct_urls:
            return None
        if _looks_like_direct_image(url):
            self._direct_urls.add(key)
            return None
        if await _is_image_response(url, client):
            self._direct_urls.add(key)
            return None
        return await self._load_page(url, client)

    async def detect(self, url: str, client: AsyncSession) -> str | None:
        """Classify ``url`` as ``"series"``, ``"gallery"``, or ``None``.

        Fetches the page once (cached on the instance) so the later
        ``scrape``/``scrape_series`` call reuses the soup instead of re-fetching.
        """
        url = normalize_url(url)
        soup = await self._resolve_page(url, client)
        if soup is None:
            return "gallery"  # direct image URL
        images = _extract_gallery_images(soup, url)
        links = _deep_links(soup, url)
        if len(links) >= SERIES_MIN_LINKS and len(images) < len(links):
            return "series"
        if images:
            return "gallery"
        return None

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        """Extract a single chapter/gallery from ``url`` as ``PostMetadata``.

        Handles three cases: a direct image URL (single-image chapter), an
        image gallery page, or a page that turns out to be a series listing
        (raises ``ValueError`` — the CLI routes series pages through
        :meth:`scrape_series` after :meth:`detect`).
        """
        url = normalize_url(url)
        soup = await self._resolve_page(url, client)
        if soup is None:
            return _single_image_meta(url)

        images = _extract_gallery_images(soup, url)
        if not images:
            raise no_images_error()

        idx = meta_index(soup)
        title = _page_title(soup, idx, url)
        series_title = _series_title_for(url, title)
        if series_title and title.startswith(series_title + " -"):
            title = title[len(series_title) + 3:].strip() or title
        return PostMetadata(
            series_title=series_title,
            chapter_title=title,
            images=_keyed_images(images),
            service=_host_of(url),
            total_pages=len(images),
            description=_description(idx),
            cover_url=_cover_url(idx, url),
        )

    async def scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        """Extract a chapter list from a series page as ``SeriesMetadata``.

        Each chapter dict carries ``title`` / ``episode_no`` / ``url``; chapter
        URLs are later handed back to :meth:`scrape` for the gallery step.
        """
        url = normalize_url(url)
        soup = await self._load_page(url, client)
        idx = meta_index(soup)
        title = _page_title(soup, idx, url)
        links = _deep_links(soup, url)
        if not links:
            raise no_chapters_error()

        chapters: list[dict] = []
        for index, (href, text) in enumerate(links):
            label = text or _path_segment(href) or href
            chapters.append({
                "title": label,
                "url": href,
                "episode_no": _episode_no(label, index + 1),
            })

        return SeriesMetadata(
            series_title=_series_title_for(url, title),
            description=_description(idx),
            cover_url=_cover_url(idx, url),
            chapters=chapters,
        )


def _keyed_images(images: list[ImageItem]) -> list[ImageItem]:
    """Re-key image filenames to the canonical ``page_%04d.ext`` layout.

    Site scrapers reach this layout through ``chapter_to_post_metadata``; the
    generic path builds ``PostMetadata`` directly, so the downloader (which
    saves by ``item.filename``) and the archiver (which re-derives the name
    via :func:`image_source_name`) must agree on the same name.
    """
    return [
        ImageItem(
            url=img.url,
            page_number=img.page_number,
            filename=image_source_name(img.page_number, img.url),
        )
        for img in images
    ]


def _single_image_meta(url: str) -> PostMetadata:
    """A single-image chapter for a direct image URL."""
    item = ImageItem.from_url(url, 1)
    if item is None:
        item = ImageItem(url=url, page_number=1, filename="page_0001.jpg")
    title = _path_segment(url) or item.filename.rsplit(".", 1)[0] or _host_of(url)
    return PostMetadata(
        series_title=_host_of(url),
        chapter_title=title,
        images=_keyed_images([item]),
        service=_host_of(url),
        total_pages=1,
    )


async def _is_image_response(url: str, client: AsyncSession) -> bool:
    """Best-effort ``HEAD`` sniff for extension-less direct image URLs.

    Any failure (connection, method not allowed, timeout) falls through to the
    HTML gallery path — the page is still fetched and parsed normally.
    """
    try:
        resp = await BaseScraper._timeout_get(url, client, method="HEAD")
    # Sniffing is best-effort.
    except Exception:  # nosec B110
        return False
    headers = getattr(resp, "headers", None) or {}
    return str(headers.get("content-type", "")).lower().startswith("image/")


# The single generic fallback instance, registered apart from the domain map.
# Importing ``comic_dl.scrapers`` (which the CLI always does) wires it in.
_generic_scraper = GenericScraper()
register_generic(_generic_scraper)
