"""Shared scraper base for VComics-platform scanlators (SSR + JSON API).

Vortex Scans / Nyx Scans render series pages server-side but only embed
the latest chapters; the full chapter list comes from the site's JSON API
(``api.<domain>/api/chapters?postId=<id>``, paginated by ``skip``). The
numeric post id is embedded in the series page's TanStack router stream
(``post:$R[N]={id:NNN,slug:"<slug>"}``). Reader pages are plain SSR HTML
with ``img[data-reader-page-image]`` in document order.

This module is underscore-prefixed so auto-discovery skips it; concrete
per-domain subclasses live in their own modules.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from ...errors import ScrapeError
from ...models import (
    ChapterInfo,
    ImageItem,
    PostMetadata,
    ScrapedChapter,
    SeriesMetadata,
    SourceInfo,
    chapter_to_post_metadata,
)
from ..base import (
    BaseScraper,
    _attr_text,
    listing_page_error,
    meta_get,
    meta_index,
    no_chapters_error,
    no_images_error,
)

_CHAPTER_SLUG_RE = re.compile(r"chapter-([\d.]+)", re.IGNORECASE)
_POST_ID_RE = re.compile(r"post:\$R\[\d+\]=\{id:(\d+),slug:\"([^\"]+)\"")
_POST_CONTENT_RE = re.compile(r'postContent:"((?:[^"\\]|\\.)*)"')
_GENRES_REF_RE = re.compile(r"genres:\$R\[(\d+)\]=\[")
_HEX_ESCAPE_RE = re.compile(r"\\x([0-9a-fA-F]{2})")
_LOCKED_BADGE_RE = re.compile(r"Locked Chapter")
_LOCK_PRICE_RE = re.compile(r"(\d+)\s+coins", re.IGNORECASE)
_OG_GENRES_RE = re.compile(r"Genres:\s*(.+?)\.\s*(?:Type:|$)", re.IGNORECASE | re.DOTALL)

_READER_IMG_SEL = "img[data-reader-page-image]"
_CHAPTER_LINK_SEL = 'a[href*="/chapter-"]'

_API_PAGE_SIZE = 10


def series_url_re(domain: str) -> re.Pattern:
    """Series-page pattern for a VComics domain (chapter URLs excluded)."""
    escaped = re.escape(domain)
    return re.compile(rf"^https?://(?:www\.)?{escaped}/series/[^/]+/?$")


def chapter_url_re(domain: str) -> re.Pattern:
    """Chapter-page pattern for a VComics domain."""
    escaped = re.escape(domain)
    return re.compile(rf"^https?://(?:www\.)?{escaped}/series/[^/]+/chapter-[\d.]+/?$")


def _series_slug_from_url(url: str) -> str:
    parts = [p for p in urlparse(url).path.rstrip("/").split("/") if p]
    try:
        i = parts.index("series")
    except ValueError:
        return ""
    return parts[i + 1] if i + 1 < len(parts) else ""


def _post_id_from_series_html(raw: str, slug: str) -> int | None:
    """Numeric API post id for ``slug`` from the router stream (exact match)."""
    for m in _POST_ID_RE.finditer(raw):
        if m.group(2) == slug:
            return int(m.group(1))
    return None


def _decode_rsc_string(raw: str) -> str:
    """Decode JS-string escapes (``\\xNN``, ``\\n``, ``\\"``) in stream text."""
    text = _HEX_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), raw)
    return text.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")


def _extract_description(raw: str, soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    """Full synopsis from the router stream's ``postContent`` HTML.

    Falls back to ``og:description``, which the site truncates with ``…``.
    """
    m = _POST_CONTENT_RE.search(raw)
    if m:
        text = BeautifulSoup(_decode_rsc_string(m.group(1)), "lxml").get_text("\n", strip=True)
        if text:
            return text
    return meta_get(idx, "og:description", "twitter:description")


def _extract_cover(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    """Series cover: the ``Cover of …`` image, not the og API card.

    ``og:image`` points at a generated share card
    (``/api/og-image/…``); the real cover carries ``alt="Cover of <title>"``.
    """
    for img in soup.select("img[alt]"):
        if _attr_text(img.get("alt")).startswith("Cover of "):
            src = _attr_text(img.get("src"))
            if src:
                return src
    return meta_get(idx, "og:image", "twitter:image")


def _locked_price(soup: BeautifulSoup) -> str | None:
    """Coin price of a locked chapter, or ``None`` when not a lock wall.

    The badge may be inline in a larger text node, so this matches the
    substring — safe because callers only consult it when the page yielded
    no images (which fails either way; this only picks the error message).
    """
    if not _LOCKED_BADGE_RE.search(soup.get_text(" ", strip=True)):
        return None
    m = _LOCK_PRICE_RE.search(soup.get_text(" ", strip=True))
    return m.group(1) if m else ""


def _normalize_genre(name: str) -> str:
    name = name.strip()
    return name[:1].upper() + name[1:] if name else ""


def _extract_genres(raw: str) -> list[str]:
    """Genre names from the router stream (``genres:$R[N]=[...]``)."""
    m = _GENRES_REF_RE.search(raw)
    if not m:
        return []
    start = raw.find(f"$R[{m.group(1)}]=[", m.end())
    if start == -1:
        return []
    j = start + len(m.group(1)) + 5  # at '['
    n = len(raw)
    depth, in_str = 0, False
    while j < n:
        ch = raw[j]
        if ch == "\\" and j + 1 < n:
            j += 2
            continue
        if ch == '"':
            in_str = not in_str
        elif not in_str and ch == "[":
            depth += 1
        elif not in_str and ch == "]":
            depth -= 1
            if depth == 0:
                break
        j += 1
    blob = raw[start + len(m.group(1)) + 5 : j + 1]
    names = re.findall(r'name:"([^"]*)"', blob)
    return [g for g in (_normalize_genre(x) for x in names) if g]


def _extract_genres_from_og(text: str) -> list[str]:
    """Genre names from an ``og:description`` ``Genres: a, b.`` segment."""
    m = _OG_GENRES_RE.search(text or "")
    if not m:
        return []
    return [g for g in (_normalize_genre(x) for x in m.group(1).split(",")) if g]


def _chapter_number(chapter: dict) -> str | None:
    number = chapter.get("number")
    if isinstance(number, bool):
        return None
    if isinstance(number, (int, float)):
        return str(int(number)) if float(number).is_integer() else str(number)
    slug = chapter.get("slug") or ""
    m = _CHAPTER_SLUG_RE.search(slug)
    return m.group(1) if m else None


def _extract_series_title(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    h1 = soup.select_one('h1[itemprop="name"]') or soup.select_one("h1")
    if h1 is not None:
        text = h1.get_text(strip=True)
        if text:
            return text
    page_title = meta_get(idx, "og:title", "twitter:title")
    if not page_title:
        title_tag = soup.select_one("title")
        page_title = title_tag.get_text(strip=True) if title_tag else ""
    return page_title.split("|")[0].strip()


def _extract_images(soup: BeautifulSoup, base: str) -> list[ImageItem]:
    items: list[ImageItem] = []
    seen: set[str] = set()
    for n, img in enumerate(soup.select(_READER_IMG_SEL), start=1):
        src = _attr_text(img.get("src")) or _attr_text(img.get("data-src"))
        if not src or src in seen or src.startswith("data:"):
            continue
        seen.add(src)
        item = ImageItem.from_url(urljoin(base, src), n)
        if item is not None:
            items.append(item)
    return items


class VComicsScraper(BaseScraper):
    """VComics chapter + series scraper, parameterized by domain.

    Subclasses set ``domain``/``name``/``site_id``/``api_base``/
    ``site_label`` and register themselves; all behavior lives here.
    """

    domain: str = ""
    api_base: str = ""
    site_label: str = ""

    def matches_url(self, url: str) -> bool:
        return self._is_chapter(url) or self._is_series(url)

    def matches_series_url(self, url: str) -> bool:
        return self._is_series(url)

    def _is_chapter(self, url: str) -> bool:
        return bool(chapter_url_re(self.domain).match(url))

    def _is_series(self, url: str) -> bool:
        return bool(series_url_re(self.domain).match(url)) and not self._is_chapter(url)

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        return await self._scrape_series(url, client)

    async def _api_json(self, url: str, client: AsyncSession) -> dict | list:
        resp = await BaseScraper._timeout_get(url, client, expect_json=True)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, (dict, list)) else {}

    async def _fetch_chapters(self, post_id: int, client: AsyncSession) -> list[dict]:
        """Full chapter list, oldest first (API pages newest-first by tens)."""
        chapters: list[dict] = []
        skip = 0
        while True:
            data = await self._api_json(
                f"{self.api_base}/api/chapters?postId={post_id}&order=asc&skip={skip}",
                client,
            )
            page = data.get("post", {}).get("chapters", []) if isinstance(data, dict) else []
            if not isinstance(page, list) or not page:
                break
            chapters.extend(c for c in page if isinstance(c, dict))
            total = data.get("totalChapterCount") if isinstance(data, dict) else None
            if len(page) < _API_PAGE_SIZE:
                break
            if isinstance(total, int) and len(chapters) >= total:
                break
            skip += _API_PAGE_SIZE
        return chapters

    async def _scrape_chapter(self, url: str, client: AsyncSession) -> ScrapedChapter:
        if not self._is_chapter(url):
            raise listing_page_error(self.site_label or self.domain, url)
        soup, _raw = await self.fetch_html_raw(url, client)
        idx = meta_index(soup)

        images = _extract_images(soup, url)
        if not images:
            price = _locked_price(soup)
            if price is not None:
                raise ScrapeError(
                    "This chapter is locked" + (f" ({price} coins)" if price else "") + ".",
                    hint="Log in (or unlock it) in a browser, then run again — "
                    "locked chapters need an unlocked session.",
                )
            raise no_images_error()

        series_title = _extract_series_title(soup, idx)
        number = _chapter_number_from_slug(url)
        if number:
            # Reader pages lack the series h1; the og title carries the
            # chapter suffix ("Series Chapter N").
            series_title = (
                re.sub(
                    rf"\s+chapter\s+{re.escape(number)}\s*$",
                    "",
                    series_title,
                    flags=re.IGNORECASE,
                ).strip()
                or series_title
            )
        title_tag = soup.select_one("title")
        page_title = title_tag.get_text(strip=True) if title_tag else ""
        chapter_title = page_title.split("|")[0].strip() or series_title
        if series_title and chapter_title.startswith(series_title):
            rest = chapter_title[len(series_title) :].lstrip(" -").strip()
            chapter_title = rest or chapter_title

        lang = ""
        html_tag = soup.select_one("html")
        if html_tag and html_tag.get("lang"):
            lang = _attr_text(html_tag.get("lang")).split("-")[0].lower()

        # Reader pages carry no synopsis or genre data of their own — the
        # og:description is SEO boilerplate ("Read … Genres: … Type: …").
        # Enrich from the series page (disk-cached after the first fetch).
        description = ""
        genres = _extract_genres_from_og(meta_get(idx, "og:description"))
        series_meta = await self._series_meta(_series_slug_from_url(url), client)
        if series_meta:
            description = series_meta["description"]
            genres = series_meta["genres"] or genres

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=series_title or "Untitled",
                chapter_title=chapter_title or "Chapter",
                chapter_number=number,
                description=description,
                genres=genres,
                language=lang,
                reading_direction="ltr",
                total_pages=len(images),
            ),
            source=SourceInfo(url=url, service=self.domain, post_id=url),
            images=images,
            cover_url=_extract_cover(soup, idx),
        )

    async def _series_meta(self, slug: str, client: AsyncSession) -> dict:
        """Description + genres for ``slug`` from its series page (best-effort)."""
        if not slug:
            return {}
        try:
            soup, raw = await self.fetch_html_raw(f"https://{self.domain}/series/{slug}", client)
        except Exception:
            return {}
        idx = meta_index(soup)
        return {
            "description": _extract_description(raw, soup, idx),
            "genres": _extract_genres(raw)
            or _extract_genres_from_og(meta_get(idx, "og:description")),
        }

    async def _scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        if not self._is_series(url):
            raise listing_page_error(self.site_label or self.domain, url)
        slug = _series_slug_from_url(url)
        if not slug:
            raise listing_page_error(self.site_label or self.domain, url)
        soup, raw = await self.fetch_html_raw(url, client)
        idx = meta_index(soup)

        series_title = _extract_series_title(soup, idx)

        chapters: list[dict] = []
        seen: set[str] = set()
        post_id = _post_id_from_series_html(raw, slug)
        if post_id is not None:
            for entry in await self._fetch_chapters(post_id, client):
                # Locked chapters need coins/login; like tapas/valirscans,
                # listings cover only what anonymous runs can download.
                if entry.get("isLocked") or entry.get("isAccessible") is False:
                    continue
                number = _chapter_number(entry)
                chapter_slug = str(entry.get("slug") or "")
                if not chapter_slug:
                    continue
                chapter_url = urljoin(url.rstrip("/") + "/", chapter_slug)
                if chapter_url in seen:
                    continue
                seen.add(chapter_url)
                title = str(entry.get("title") or "").strip() or (
                    f"Chapter {number}" if number else chapter_slug
                )
                chapters.append({"title": title, "url": chapter_url, "episode_no": number or title})
        if not chapters:
            for link in soup.select(_CHAPTER_LINK_SEL):
                href = _attr_text(link.get("href"))
                if not href:
                    continue
                chapter_url = urljoin(url, href)
                if chapter_url in seen or not chapter_url_re(self.domain).match(chapter_url):
                    continue
                seen.add(chapter_url)
                number = _chapter_number_from_slug(chapter_url)
                raw_title = link.get_text(strip=True)
                chapters.append(
                    {
                        "title": raw_title or f"Chapter {number}",
                        "url": chapter_url,
                        "episode_no": number or raw_title,
                    }
                )

        if not chapters:
            raise no_chapters_error()

        return SeriesMetadata(
            series_title=series_title or slug.replace("-", " ").title(),
            description=_extract_description(raw, soup, idx),
            cover_url=_extract_cover(soup, idx),
            title_no=slug,
            chapters=chapters,
        )


def _chapter_number_from_slug(url: str) -> str | None:
    m = _CHAPTER_SLUG_RE.search(url.rstrip("/"))
    return m.group(1) if m else None
