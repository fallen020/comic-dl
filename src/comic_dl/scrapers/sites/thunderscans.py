"""Thunderscans EN (en-thunderscans.com) scraper for its mangareader theme.

Series pages (``/comics/{slug}/``) statically render the title, synopsis,
cover, genres, status, rating, and the full chapter list inside
``#chapterlist``. Chapter pages (``/{slug}-chapter-{n}/``) embed the page
images as a ``ts_reader.run({...})`` JSON blob — the ``#readerarea`` div is
populated by JavaScript, so the blob is the only static source. Locked
chapters carry no blob and no images.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

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
    no_chapters_error,
    no_images_error,
)
from ..registry import register_scraper

DOMAIN = "en-thunderscans.com"
BASE = "https://en-thunderscans.com"

_SERIES_PATH_RE = re.compile(
    r"^https?://(?:www\.)?en-thunderscans\.com/comics/[^/]+/?$"
)

_CHAPTER_PATH_RE = re.compile(
    r"^https?://(?:www\.)?en-thunderscans\.com/[^/]+-chapter-\d+(?:[-.]\d+)*/?$"
)

_CHAPTER_NUM_RE = re.compile(r"-chapter-(\d+(?:[-.]\d+)?)/?$", re.IGNORECASE)

_TS_READER_MARK = "ts_reader.run("

_TITLE_SUFFIXES = ("\u2013 Thunderscans EN", " | Thunderscans EN", " - Thunderscans EN")


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a series page for this source."""
    return bool(_SERIES_PATH_RE.match(url))


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a chapter page for this source."""
    return bool(_CHAPTER_PATH_RE.match(url))


def _on_site_host(raw: str) -> bool:
    host = (urlparse(raw).hostname or "").lower()
    return host == DOMAIN or host.endswith("." + DOMAIN)


def _series_slug_from_url(url: str) -> str:
    """Series slug from a series or chapter URL."""
    parts = [p for p in url.rstrip("/").split("/") if p]
    if not parts:
        return ""
    last = parts[-1]
    if len(parts) >= 2 and parts[-2] == "comics":
        return last
    cut = re.search(r"-chapter-\d+(?:[-.]\d+)*$", last)
    if cut:
        return last[: cut.start()]
    return ""


def _chapter_number_from_url(url: str) -> str | None:
    """Display number from a chapter URL (``-33-1`` decodes to ``33.1``)."""
    m = _CHAPTER_NUM_RE.search(url.rstrip("/"))
    if not m:
        return None
    return m.group(1).replace("-", ".")


def _ts_reader_index(raw: str) -> dict | None:
    """The JSON config inside ``ts_reader.run({...})``, if any.

    Scans with a quote-aware brace counter instead of a regex so the JSON
    closes exactly where it opens, whatever the payload contains.
    """
    start = raw.find(_TS_READER_MARK)
    if start < 0:
        return None
    open_idx = raw.find("{", start + len(_TS_READER_MARK))
    if open_idx < 0:
        return None
    depth = 0
    in_str = False
    escaped = False
    for i in range(open_idx, len(raw)):
        ch = raw[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[open_idx : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _extract_images(raw: str) -> list[ImageItem]:
    """Page images from the chapter page's ``ts_reader`` config."""
    data = _ts_reader_index(raw)
    if data is None:
        return []
    images: list[ImageItem] = []
    seen: set[str] = set()
    for source in data.get("sources") or []:
        for url in source.get("images") or []:
            if not isinstance(url, str):
                continue
            clean = BaseScraper.clean_image_url(url)
            if not clean or clean in seen or not _on_site_host(clean):
                continue
            seen.add(clean)
            images.append(ImageItem(url=clean, page_number=len(images) + 1))
    return images


def _extract_series_title(soup: BeautifulSoup) -> str:
    h1 = soup.select_one("h1.entry-title")
    if h1 is not None:
        text = h1.get_text(strip=True)
        if text:
            return text
    title_tag = soup.select_one("title")
    page_title = title_tag.get_text(strip=True) if title_tag else ""
    for sep in _TITLE_SUFFIXES:
        if page_title.endswith(sep):
            page_title = page_title[: -len(sep)].strip()
            break
    return page_title


def _extract_description(soup: BeautifulSoup) -> str:
    el = soup.select_one(".main-info .summary .entry-content")
    if el is not None:
        return el.get_text(" ", strip=True)
    return ""


def _extract_cover(soup: BeautifulSoup) -> str:
    img = soup.select_one(".main-info .thumb img")
    if img is not None:
        src = _attr_text(img.get("src"))
        if src and not src.startswith("data:"):
            return BaseScraper.clean_image_url(src)
    return ""


def _extract_genres(soup: BeautifulSoup) -> list[str]:
    return [
        a.get_text(strip=True)
        for a in soup.select('.main-info .mgen a[href*="/genres/"]')
        if a.get_text(strip=True)
    ]


def _extract_status(soup: BeautifulSoup) -> str | None:
    el = soup.select_one(".main-info .status")
    if el is not None:
        text = el.get_text(" ", strip=True)
        if text:
            return text
    return None


def _extract_rating(soup: BeautifulSoup) -> float | None:
    el = soup.select_one(".main-info .numscore")
    if el is not None:
        try:
            return float(el.get_text(strip=True))
        except ValueError:
            pass
    return None


def _series_page(soup: BeautifulSoup) -> dict:
    """Header metadata from a series page (shared by both scrape modes)."""
    return {
        "series_title": _extract_series_title(soup),
        "description": _extract_description(soup),
        "cover_url": _extract_cover(soup),
        "genres": _extract_genres(soup),
        "status": _extract_status(soup),
        "community_rating": _extract_rating(soup),
    }


def _extract_chapter_title(soup: BeautifulSoup, series_title: str) -> str:
    """Chapter title from the ``h1``, minus the series-name prefix."""
    h1 = soup.select_one("h1.entry-title")
    chapter_title = h1.get_text(" ", strip=True) if h1 is not None else ""
    if series_title and chapter_title.startswith(series_title):
        rest = chapter_title[len(series_title):].lstrip(" -:").strip()
        if rest:
            return rest
    return chapter_title


def _chapter_entry_title(number: str) -> str:
    return f"Chapter {number}"


def _sort_key(item: dict) -> float:
    try:
        return float(item["episode_no"])
    except (TypeError, ValueError):
        return float("inf")


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class ThunderscansScraper(BaseScraper):
    """Thunderscans EN chapter and series scraper."""

    domain = DOMAIN
    name = "thunderscans"

    def __init__(self) -> None:
        super().__init__()
        self._series_cache: dict[str, dict] = {}

    def matches_url(self, url: str) -> bool:
        return is_chapter_url(url) or is_series_url(url)

    def matches_series_url(self, url: str) -> bool:
        return is_series_url(url)

    @staticmethod
    async def _fetch(url: str, client: AsyncSession) -> tuple[BeautifulSoup, str]:
        return await BaseScraper.fetch_html_raw(url, client)

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        return await self._scrape_series(url, client)

    async def _series_page_data(self, series_slug: str, client: AsyncSession) -> dict:
        """Fetch the series page once per slug (cached on the instance).

        The chapter page carries no author/status/genres fields; those live
        only on the series page. Best-effort — a failed fetch degrades to
        empty enrichment.
        """
        cached = self._series_cache.get(series_slug)
        if cached is not None:
            return cached
        data: dict = {}
        try:
            response = await BaseScraper._timeout_get(
                f"{BASE}/comics/{series_slug}/", client
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            data = _series_page(soup)
        # Enrichment is best-effort.
        except Exception:  # nosec
            pass
        self._series_cache[series_slug] = data
        return data

    async def _scrape_chapter(
        self, url: str, client: AsyncSession,
    ) -> ScrapedChapter:
        soup, raw = await self._fetch(url, client)

        images = _extract_images(raw)
        if not images:
            raise no_images_error()

        series_slug = _series_slug_from_url(url)
        series = await self._series_page_data(series_slug, client) if series_slug else {}

        series_title = series.get("series_title", "")
        chapter_number = _chapter_number_from_url(url)
        chapter_title = _extract_chapter_title(soup, series_title)
        if not chapter_title:
            chapter_title = (
                f"Chapter {chapter_number}" if chapter_number else "Chapter"
            )

        data = _ts_reader_index(raw)
        post_id = str(data.get("post_id") or "") if data else ""

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=series_title or "Untitled",
                chapter_title=chapter_title or "Chapter",
                chapter_number=chapter_number,
                description=series.get("description", ""),
                genres=series.get("genres", []),
                status=series.get("status"),
                community_rating=series.get("community_rating"),
                language="en",
                reading_direction="ltr",
                total_pages=len(images),
            ),
            source=SourceInfo(url=url, service=DOMAIN, post_id=post_id),
            images=images,
            cover_url=series.get("cover_url", ""),
        )

    async def _scrape_series(
        self, url: str, client: AsyncSession,
    ) -> SeriesMetadata:
        soup, _ = await self._fetch(url, client)

        page = _series_page(soup)
        slug = _series_slug_from_url(url)

        chapters: list[dict] = []
        seen: set[str] = set()
        for li in soup.select("#chapterlist ul li"):
            href = ""
            link = li.select_one("a[href]")
            if link is not None:
                href = _attr_text(link.get("href"))
            number = _attr_text(li.get("data-num")) or _chapter_number_from_url(href)
            if not number or not href or href in seen:
                continue
            seen.add(href)
            chapters.append({
                "title": _chapter_entry_title(number),
                "url": urljoin(url, href),
                "episode_no": number,
            })

        if not chapters:
            raise no_chapters_error()

        chapters.sort(key=_sort_key)

        return SeriesMetadata(
            series_title=page["series_title"] or (
                slug.replace("-", " ").title() if slug else "Untitled"
            ),
            description=page["description"],
            cover_url=page["cover_url"],
            title_no=slug,
            chapters=chapters,
        )
