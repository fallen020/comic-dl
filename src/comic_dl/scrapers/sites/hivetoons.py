"""HiveToons (hivetoons.org) scraper for its Astro reader.

Series pages (``/series/{slug}/``) carry the title, synopsis, cover, author,
status, genres, and the chapter list as server-rendered HTML. Chapter pages
(``/series/{slug}/chapter-{n}/``) render page images inside
``.comic-images-wrapper`` and an ``Article`` JSON-LD headline; series-level
fields come from a cached best-effort fetch of the series page.
"""

from __future__ import annotations

import html
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
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
    article_jsonld_nodes,
    meta_get,
    meta_index,
    no_chapters_error,
    no_images_error,
)
from ..registry import register_scraper

DOMAIN = "hivetoons.org"
BASE = "https://hivetoons.org"

_SERIES_PATH_RE = re.compile(
    r"^https?://(?:www\.)?hivetoons\.org/series/[^/]+/?$"
)

_CHAPTER_PATH_RE = re.compile(
    r"^https?://(?:www\.)?hivetoons\.org/series/[^/]+/chapter-\d+(?:\.\d+)?/?$"
)

_CHAPTER_NUM_RE = re.compile(r"/chapter-(\d+(?:\.\d+)?)/?$")

_READER_SEL = ".comic-images-wrapper"

# The series page renders only the newest chapters as links; the full list
# (number/slug/title/createdAt per chapter) is embedded as serialized page
# state, HTML-escaped.
_EMBEDDED_CHAPTER_RE = re.compile(
    r'"number":\[0,([\d.]+)\],"slug":\[0,"(chapter-[\d.]+)"\],'
    r'"title":\[0,"([^"]*)"\]'
)
_EMBEDDED_LOCK_RE = re.compile(
    r'"slug":\[0,"(chapter-[\d.]+)"\].{0,2000}?"isLocked":\[0,(true|false)\]'
)

_STAT_LABELS = ("div", "span", "dt", "dd", "h1", "h5", "h6")


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a series page for this source."""
    return bool(_SERIES_PATH_RE.match(url))


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a chapter/gallery page for this source."""
    return bool(_CHAPTER_PATH_RE.match(url))


def _clean_image_url(raw: str) -> str:
    """Strip the cache-buster query (and any fragment) from an image URL."""
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
    h1 = soup.select_one("h1.break-words")
    if h1 is not None:
        text = h1.get_text(strip=True)
        if text:
            return text
    page_title = meta_get(idx, "og:title", "twitter:title")
    if not page_title:
        title_tag = soup.select_one("title")
        page_title = title_tag.get_text(strip=True) if title_tag else ""
    for sep in (" Manhwa", " - Hive Toon", " | Hive Toon"):
        if page_title.endswith(sep):
            page_title = page_title[: -len(sep)].strip()
    return page_title


def _extract_description(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    return meta_get(idx, "og:description", "twitter:description", "description")


def _extract_cover(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    img = soup.select_one('img[alt^="Cover of "]')
    if img is not None:
        src = _attr_text(img.get("src"))
        if src and not src.startswith("data:"):
            return _clean_image_url(src)
    content = meta_get(idx, "og:image", "twitter:image")
    if content:
        return _clean_image_url(content.split(",")[0].strip())
    return ""


def _extract_stat(soup: BeautifulSoup, label: str) -> str | None:
    """Value of the stat card whose heading is exactly ``label``.

    The tightest matching card wins: a stray top-level heading falls back
    to its whole section instead of swallowing the page.
    """
    best: str | None = None
    for el in soup.select(", ".join(_STAT_LABELS)):
        if el.get_text(strip=True) != label:
            continue
        parent = el.parent
        if parent is None:
            continue
        text = parent.get_text(" ", strip=True)
        value = text[len(label):].strip()
        if value and (best is None or len(text) < len(best) + len(label)):
            best = value
    return best


def _extract_authors(soup: BeautifulSoup) -> list[str]:
    authors = []
    for label in ("Author", "Artist"):
        value = _extract_stat(soup, label)
        if value:
            authors.append(value)
    return list(dict.fromkeys(authors))


def _extract_status(soup: BeautifulSoup) -> str | None:
    return _extract_stat(soup, "Status")


def _extract_genres(soup: BeautifulSoup) -> list[str]:
    return [
        a.get_text(strip=True)
        for a in soup.select('a[href*="/series?genres="]')
        if a.get_text(strip=True)
    ]


def _extract_lang(soup: BeautifulSoup) -> str:
    html_tag = soup.select_one("html")
    if html_tag and html_tag.get("lang"):
        return _attr_text(html_tag.get("lang")).split("-")[0].lower()
    return ""


def _chapter_link_title(link: Tag) -> tuple[str | None, str]:
    """``(number, subtitle)`` for a series-page chapter link."""
    href = _attr_text(link.get("href"))
    number = _chapter_number_from_url(href) if href else None
    subtitle_el = link.select_one("div[title]")
    subtitle = _attr_text(subtitle_el.get("title")) if subtitle_el else ""
    if not subtitle and number:
        text = link.get_text(" ", strip=True)
        rest = re.sub(rf"^Chapter\s*{re.escape(number)}\s*", "", text).strip()
        rest = re.sub(r"\s*(New|about .+ ago)$", "", rest).strip()
        subtitle = rest
    return number, subtitle


def _chapter_entry_title(number: str, subtitle: str) -> str:
    if subtitle and subtitle != f"Chapter {number}":
        return f"Chapter {number} - {subtitle}"
    return f"Chapter {number}"


def _extract_headline(soup: BeautifulSoup) -> str:
    for node in article_jsonld_nodes(soup):
        headline = _attr_text(node.get("headline"))
        if headline:
            return headline
    return ""


def _extract_chapter_title(
    soup: BeautifulSoup,
    idx: dict[str, list[str]],
    series_title: str,
) -> str:
    headline = _extract_headline(soup) or meta_get(idx, "og:title", "twitter:title")
    if not headline:
        title_tag = soup.select_one("title")
        headline = title_tag.get_text(strip=True) if title_tag else ""
    if series_title and headline.startswith(series_title):
        rest = headline[len(series_title):].lstrip(" -:").strip()
        if rest:
            return rest
    return headline or series_title


def _extract_images(soup: BeautifulSoup) -> list[ImageItem]:
    images: list[ImageItem] = []
    seen: set[str] = set()

    scope = soup.select_one(_READER_SEL)
    if scope is None:
        return images
    for img in scope.find_all("img"):
        src = _attr_text(img.get("src")) or _attr_text(img.get("data-src"))
        if not src or src.startswith("data:"):
            continue
        clean = _clean_image_url(src)
        if clean in seen:
            continue
        seen.add(clean)
        images.append(ImageItem(url=clean, page_number=len(images) + 1))

    return images


def _embedded_chapters(raw_html: str, series_slug: str) -> list[dict]:
    """Chapter entries from the series page's embedded page state.

    Only the newest chapters render as links; the payload carries every
    chapter. Locked (paywalled) chapters are skipped.
    """
    text = html.unescape(raw_html)
    locked = {
        slug for slug, flag in _EMBEDDED_LOCK_RE.findall(text) if flag == "true"
    }
    chapters: list[dict] = []
    seen: set[str] = set()
    for number, slug, subtitle in _EMBEDDED_CHAPTER_RE.findall(text):
        if slug in seen or slug in locked:
            continue
        seen.add(slug)
        chapters.append({
            "title": _chapter_entry_title(number, subtitle),
            "url": f"{BASE}/series/{series_slug}/{slug}",
            "episode_no": number,
        })
    return chapters


def _linked_chapters(
    soup: BeautifulSoup, page_url: str, series_slug: str,
) -> list[dict]:
    """Chapter entries from the series page's rendered links (fallback)."""
    chapters: list[dict] = []
    seen_urls: set[str] = set()

    marker = f"/series/{series_slug}/chapter-" if series_slug else "/chapter-"
    for link in soup.select('a[href*="/chapter-"]'):
        href = _attr_text(link.get("href"))
        if not href or href in seen_urls:
            continue
        if marker not in href:
            continue
        number, subtitle = _chapter_link_title(link)
        if number is None:
            continue
        seen_urls.add(href)
        chapters.append({
            "title": _chapter_entry_title(number, subtitle),
            "url": urljoin(page_url, href),
            "episode_no": number,
        })
    return chapters


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class HiveToonsScraper(BaseScraper):
    """HiveToons chapter and series scraper."""

    domain = DOMAIN
    name = "hivetoons"
    site_id = "hivetoons"
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
                    "page not found on HiveToons.",
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
                f"{BASE}/series/{series_slug}/", client
            )
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
            }
        # Enrichment is best-effort.
        except Exception:  # nosec
            pass
        self._series_cache[series_slug] = data
        return data

    async def _scrape_chapter(
        self, url: str, client: AsyncSession,
    ) -> ScrapedChapter:
        soup, _ = await self._fetch(url, client)
        idx = meta_index(soup)

        images = _extract_images(soup)
        if not images:
            raise no_images_error()

        series_slug = _series_slug_from_url(url)
        series = await self._series_page_data(series_slug, client) if series_slug else {}

        series_title = series.get("series_title", "")
        chapter_number = _chapter_number_from_url(url)
        chapter_title = _extract_chapter_title(soup, idx, series_title)
        if not chapter_title or chapter_title == series_title:
            chapter_title = (
                f"Chapter {chapter_number}" if chapter_number else "Chapter"
            )

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=series_title or "Untitled",
                chapter_title=chapter_title or "Chapter",
                chapter_number=chapter_number,
                description=series.get("description", ""),
                authors=series.get("authors", []),
                genres=series.get("genres", []),
                status=series.get("status"),
                language=_extract_lang(soup) or "en",
                reading_direction="ltr",
                total_pages=len(images),
            ),
            source=SourceInfo(url=url, service=DOMAIN),
            images=images,
            cover_url=series.get("cover_url", ""),
        )

    async def _scrape_series(
        self, url: str, client: AsyncSession,
    ) -> SeriesMetadata:
        soup, raw = await self._fetch(url, client)
        idx = meta_index(soup)

        series_title = _extract_series_title(soup, idx)
        description = _extract_description(soup, idx)
        cover_url = _extract_cover(soup, idx)
        title_no = _series_slug_from_url(url)

        chapters = _embedded_chapters(raw, title_no) if title_no else []
        if not chapters:
            chapters = _linked_chapters(soup, url, title_no)

        if not chapters:
            raise no_chapters_error()

        def _sort_key(item: dict) -> float:
            try:
                return float(item["episode_no"])
            except (TypeError, ValueError):
                return float("inf")

        chapters.sort(key=_sort_key)

        return SeriesMetadata(
            series_title=series_title or (
                title_no.replace("-", " ").title() if title_no else "Untitled"
            ),
            description=description,
            cover_url=cover_url,
            title_no=title_no,
            chapters=chapters,
        )
