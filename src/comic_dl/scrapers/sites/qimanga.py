"""QiScans (qimanga.com) scraper for its Angular SSR reader.

Series pages (``/series/{slug}``) server-render the title, synopsis, cover,
genres, status, author, and the newest 30 chapter rows of the list — older
chapters only load through the client-side API. Chapter pages
(``/series/{slug}/chapter-{n}``) render ``img.r-page-img`` reader images
served from ``media.qimanhwa.com`` and carry the series and chapter names in
a breadcrumb, so series-level fields come from a cached best-effort fetch of
the series page. Series scrapes therefore cover the newest SSR-published
chapters; anything older stays reachable through direct chapter URLs.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError

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
    meta_get,
    meta_index,
    no_chapters_error,
    no_images_error,
)
from ..registry import register_scraper

DOMAIN = "qimanga.com"
BASE = "https://qimanga.com"

_SERIES_PATH_RE = re.compile(r"^https?://(?:www\.)?qimanga\.com/series/[^/]+/?$")

_CHAPTER_PATH_RE = re.compile(
    r"^https?://(?:www\.)?qimanga\.com/series/[^/]+/chapter-\d+(?:\.\d+)?/?$"
)

_CHAPTER_NUM_RE = re.compile(r"/chapter-(\d+(?:\.\d+)?)/?$")

_READER_IMG_SEL = "img.r-page-img"

_YEAR_RE = re.compile(r"^\d{4}$")

# Values the site uses for "nothing listed here" placeholders.
_PLACEHOLDER_VALUES = frozenset({"N/A", "Unknown"})


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a series page for this source."""
    return bool(_SERIES_PATH_RE.match(url))


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a chapter/gallery page for this source."""
    return bool(_CHAPTER_PATH_RE.match(url))


def _clean_image_url(raw: str) -> str:
    """Strip the query/cache-buster (and any fragment) from an image URL."""
    return raw.split("?")[0].split("#")[0]


def _series_slug_from_url(url: str) -> str:
    parts = [p for p in url.rstrip("/").split("/") if p]
    try:
        i = parts.index("series")
    except ValueError:
        return ""
    if i + 1 < len(parts):
        return parts[i + 1]
    return ""


def _chapter_number_from_url(url: str) -> str | None:
    m = _CHAPTER_NUM_RE.search(url.rstrip("/"))
    return m.group(1) if m else None


def _extract_series_title(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    h1 = soup.select_one("h1.series-title")
    if h1 is not None:
        text = h1.get_text(strip=True)
        if text:
            return text
    return meta_get(idx, "og:title", "twitter:title")


def _extract_description(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    el = soup.select_one(".description")
    if el is not None:
        text = el.get_text(" ", strip=True)
        if text:
            return text
    return meta_get(idx, "og:description", "twitter:description", "description")


def _extract_cover(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    img = soup.select_one(".cover-wrap img.cover-img")
    if img is not None:
        src = _attr_text(img.get("src"))
        if src and not src.startswith("data:"):
            return _clean_image_url(src)
    content = meta_get(idx, "og:image", "twitter:image")
    if content:
        return _clean_image_url(content.split(",")[0].strip())
    return ""


def _extract_genres(soup: BeautifulSoup) -> list[str]:
    return [a.get_text(strip=True) for a in soup.select("a.genre-tag") if a.get_text(strip=True)]


def _status_item_value(soup: BeautifulSoup, label: str) -> str | None:
    """Value of the ``.status-item`` whose ``.status-label`` is exactly ``label``."""
    for item in soup.select(".status-item"):
        lbl = item.select_one(".status-label")
        if lbl is None or lbl.get_text(strip=True) != label:
            continue
        val = item.select_one(".status-value, .badge")
        if val is not None:
            text = val.get_text(strip=True)
            if text and text not in _PLACEHOLDER_VALUES:
                return text
    return None


def _extract_authors(soup: BeautifulSoup) -> list[str]:
    value = _status_item_value(soup, "Author")
    return [value] if value else []


def _extract_status(soup: BeautifulSoup) -> str | None:
    return _status_item_value(soup, "Title Status")


def _extract_year(soup: BeautifulSoup) -> int | None:
    value = _status_item_value(soup, "Release Year")
    if value and _YEAR_RE.match(value):
        return int(value)
    return None


def _extract_rating(soup: BeautifulSoup) -> float | None:
    el = soup.select_one("span.rating-number")
    if el is None:
        return None
    try:
        return float(el.get_text(strip=True))
    except ValueError:
        return None


def _extract_lang(soup: BeautifulSoup) -> str:
    html_tag = soup.select_one("html")
    if html_tag and html_tag.get("lang"):
        return _attr_text(html_tag.get("lang")).split("-")[0].lower()
    return ""


def _breadcrumb_series_title(soup: BeautifulSoup) -> str:
    """Series name from the reader breadcrumb (skipping the home link)."""
    for a in soup.select("a.r-breadcrumb-link"):
        if "r-breadcrumb-home" not in (a.get("class") or []):
            text = a.get_text(strip=True)
            if text:
                return text
    return ""


def _extract_chapter_title(
    soup: BeautifulSoup,
    chapter_number: str | None,
) -> str:
    span = soup.select_one("span.r-breadcrumb-chapter")
    if span is not None:
        text = span.get_text(strip=True)
        if text:
            # The site's own "Ch. 200" label; the chapter list spells the same
            # chapter "Chapter 200", so normalize for one consistent title.
            if re.match(r"Ch\.?\s*\d", text) and chapter_number:
                return f"Chapter {chapter_number}"
            return text
    return f"Chapter {chapter_number}" if chapter_number else "Chapter"


def _extract_images(soup: BeautifulSoup) -> list[ImageItem]:
    images: list[ImageItem] = []
    seen: set[str] = set()

    for img in soup.select(_READER_IMG_SEL):
        src = _attr_text(img.get("src")) or _attr_text(img.get("data-src"))
        if not src or src.startswith("data:"):
            continue
        clean = _clean_image_url(src)
        if clean in seen:
            continue
        seen.add(clean)
        images.append(ImageItem(url=clean, page_number=len(images) + 1))

    return images


def _chapter_sort_key(item: dict) -> float:
    try:
        return float(item["episode_no"])
    except (TypeError, ValueError):
        return float("inf")


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class QiMangaScraper(BaseScraper):
    """QiScans chapter and series scraper."""

    domain = DOMAIN
    name = "qimanga"
    site_id = "qimanga"
    version = "1.0.0"
    minimum_core_version = "0.0.2"

    def __init__(self) -> None:
        super().__init__()
        self._series_cache: dict[str, dict] = {}

    def matches_url(self, url: str) -> bool:
        return is_chapter_url(url) or is_series_url(url)

    def matches_series_url(self, url: str) -> bool:
        return is_series_url(url)

    @staticmethod
    async def _fetch(url: str, client: AsyncSession) -> tuple[BeautifulSoup, str]:
        """Fetch a page, turning a 404 into a friendly removal error."""
        try:
            return await BaseScraper.fetch_html_raw(url, client)
        except CurlHTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 404:
                raise ScrapeError(
                    "page not found on QiScans.",
                    hint="the series may have been removed, or this chapter link is dead.",
                ) from None
            raise

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        return await self._scrape_series(url, client)

    async def _series_page_data(self, series_slug: str, client: AsyncSession) -> dict:
        """Fetch the series page once per slug (cached on the instance).

        The chapter page carries only the breadcrumb names; authors, genres,
        status, synopsis, and cover live on the series page. Best-effort — a
        failed fetch degrades to empty enrichment.
        """
        cached = self._series_cache.get(series_slug)
        if cached is not None:
            return cached
        data: dict = {}
        try:
            response = await BaseScraper._timeout_get(f"{BASE}/series/{series_slug}", client)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            idx = meta_index(soup)
            data = {
                "series_title": _extract_series_title(soup, idx),
                "description": _extract_description(soup, idx),
                "cover_url": _extract_cover(soup, idx),
                "genres": _extract_genres(soup),
                "authors": _extract_authors(soup),
                "status": _extract_status(soup),
                "community_rating": _extract_rating(soup),
                "year": _extract_year(soup),
            }
        # Enrichment is best-effort.
        except Exception:  # nosec B110
            pass
        self._series_cache[series_slug] = data
        return data

    async def _scrape_chapter(
        self,
        url: str,
        client: AsyncSession,
    ) -> ScrapedChapter:
        soup, _ = await self._fetch(url, client)

        images = _extract_images(soup)
        if not images:
            raise no_images_error()

        series_slug = _series_slug_from_url(url)
        series = await self._series_page_data(series_slug, client) if series_slug else {}

        series_title = _breadcrumb_series_title(soup) or series.get("series_title", "")
        chapter_number = _chapter_number_from_url(url)
        chapter_title = _extract_chapter_title(soup, chapter_number)
        if not chapter_title:
            chapter_title = f"Chapter {chapter_number}" if chapter_number else "Chapter"

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=series_title or "Untitled",
                chapter_title=chapter_title or "Chapter",
                chapter_number=chapter_number,
                description=series.get("description", ""),
                authors=series.get("authors", []),
                genres=series.get("genres", []),
                status=series.get("status"),
                community_rating=series.get("community_rating"),
                language=_extract_lang(soup) or "en",
                reading_direction="ltr",
                year=series.get("year"),
                total_pages=len(images),
            ),
            source=SourceInfo(url=url, service=DOMAIN),
            images=images,
            cover_url=series.get("cover_url", ""),
        )

    async def _scrape_series(
        self,
        url: str,
        client: AsyncSession,
    ) -> SeriesMetadata:
        soup, _ = await self._fetch(url, client)
        idx = meta_index(soup)

        series_title = _extract_series_title(soup, idx)
        description = _extract_description(soup, idx)
        cover_url = _extract_cover(soup, idx)
        title_no = _series_slug_from_url(url)

        chapters: list[dict] = []
        seen_urls: set[str] = set()

        for link in soup.select("a.cl-row"):
            href = _attr_text(link.get("href"))
            if not href or href in seen_urls:
                continue
            number = _chapter_number_from_url(href)
            if number is None:
                continue
            seen_urls.add(href)
            num_span = link.select_one("span.cl-num")
            label = ""
            if num_span is not None:
                label = num_span.get_text(strip=True)
            label = label or _attr_text(link.get("aria-label")) or f"Chapter {number}"
            chapters.append(
                {
                    "title": label,
                    "url": urljoin(url, href),
                    "episode_no": number,
                }
            )

        if not chapters:
            raise no_chapters_error()

        chapters.sort(key=_chapter_sort_key)

        return SeriesMetadata(
            series_title=series_title
            or (title_no.replace("-", " ").title() if title_no else "Untitled"),
            description=description,
            cover_url=cover_url,
            title_no=title_no,
            chapters=chapters,
        )
