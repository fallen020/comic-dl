"""Asura Scans scraper (Astro site with JSON-LD metadata and CDN images)."""

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
    extract_jsonld,
    jsonld_type_includes,
    meta_get,
    meta_index,
    no_chapters_error,
    no_images_error,
)
from ..registry import register_scraper

DOMAIN = "asurascans.com"
BASE = "https://asurascans.com"

_CDN_HOST = "cdn.asurascans.com"
_CHAPTER_PATH_MARKS = (
    "/asura-images/chapters/",
    "/asura-images/chapters-restored/",
)

_SERIES_PATH_RE = re.compile(
    r"^https?://(?:www\.)?asurascans\.com/comics/[^/]+/?$"
)

_CHAPTER_PATH_RE = re.compile(
    r"^https?://(?:www\.)?asurascans\.com/comics/[^/]+/chapter/\d+/?$"
)

_STATUS_LABEL = "Status"
_PREMIUM_MARKER = "Premium"

_STATUS_LABEL_SEL = "div, span, dt, dd, h5, h6"


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a series page for this source."""
    return bool(_SERIES_PATH_RE.match(url))


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a chapter/gallery page for this source."""
    return bool(_CHAPTER_PATH_RE.match(url))


def _clean_image_url(raw: str) -> str:
    """Strip the ``?v=...`` cache-buster (and any fragment) from an image URL."""
    return raw.split("?")[0].split("#")[0]


def _extract_node(soup: BeautifulSoup, wanted: str) -> dict | None:
    for node in extract_jsonld(soup):
        if jsonld_type_includes(node, wanted):
            return node
    return None


def _article_node(soup: BeautifulSoup) -> dict | None:
    return _extract_node(soup, "Article")


def _comic_series_node(soup: BeautifulSoup) -> dict | None:
    return _extract_node(soup, "ComicSeries")


def _person_name(value: object) -> str:
    if isinstance(value, dict):
        name = _attr_text(value.get("name"))
        if name:
            return name
    if isinstance(value, str):
        return value.strip()
    return ""


def _node_image_url(value: object) -> str:
    if isinstance(value, dict):
        return _attr_text(value.get("url"))
    if isinstance(value, str):
        return value.strip()
    return ""


def _aggregate_rating(value: object) -> float | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("ratingValue")
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return None
    if isinstance(raw, (int, float)):
        return float(raw)
    return None


def _extract_meta(soup: BeautifulSoup) -> dict:
    """Authoritative series metadata from the ``ComicSeries`` JSON-LD node."""
    node = _comic_series_node(soup)
    if node is None:
        return {}
    return {
        "series_title": _attr_text(node.get("name")),
        "description": _attr_text(node.get("description")),
        "cover_url": _node_image_url(node.get("image")),
        "genres": [g for g in node.get("genre", []) if isinstance(g, str) and g.strip()],
        "authors": [a for a in [_person_name(node.get("author"))] if a],
        "artists": [a for a in [_person_name(node.get("illustrator"))] if a],
        "community_rating": _aggregate_rating(node.get("aggregateRating")),
    }


def _site_stripped_title(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    """Page title with the trailing `` | Asura Scans`` marker removed."""
    page_title = meta_get(idx, "og:title", "twitter:title")
    if not page_title:
        title_tag = soup.select_one("title")
        page_title = title_tag.get_text(strip=True) if title_tag else ""
    for sep in (" | Asura Scans", " - Asura Scans"):
        if page_title.endswith(sep):
            return page_title[: -len(sep)].strip()
    return page_title


def _extract_series_title(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    meta = _extract_meta(soup)
    if meta.get("series_title"):
        return meta["series_title"]
    return _site_stripped_title(soup, idx)


def _extract_chapter_title(
    soup: BeautifulSoup,
    idx: dict[str, list[str]],
    series_title: str,
) -> str:
    article = _article_node(soup)
    headline = _attr_text(article.get("headline")) if article else ""
    if headline:
        if series_title and headline.startswith(series_title):
            rest = headline[len(series_title):].lstrip(" -:").strip()
            if rest:
                return rest
        return headline
    stripped = _site_stripped_title(soup, idx)
    if series_title and stripped.startswith(series_title):
        rest = stripped[len(series_title):].lstrip(" -:").strip()
        if rest:
            return rest
    if stripped:
        return stripped
    return series_title


def _extract_lang(soup: BeautifulSoup) -> str:
    html_tag = soup.select_one("html")
    if html_tag and html_tag.get("lang"):
        return _attr_text(html_tag.get("lang")).split("-")[0].lower()
    return ""


def _extract_year(soup: BeautifulSoup) -> int | None:
    article = _article_node(soup)
    if article:
        raw = _attr_text(article.get("datePublished"))
        m = re.match(r"^(\d{4})", raw)
        if m:
            return int(m.group(1))
    return None


def _extract_description(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    meta = _extract_meta(soup)
    if meta.get("description"):
        return meta["description"]
    return meta_get(idx, "og:description", "twitter:description", "description")


def _extract_cover(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    content = meta_get(idx, "og:image", "twitter:image")
    if content:
        return _clean_image_url(content.split(",")[0].strip())
    article = _article_node(soup)
    if article:
        return _clean_image_url(_node_image_url(article.get("image")))
    return _extract_meta(soup).get("cover_url", "")


def _is_premium_page(soup: BeautifulSoup, idx: dict[str, list[str]]) -> bool:
    title_tag = soup.select_one("title")
    if title_tag and _PREMIUM_MARKER in title_tag.get_text(strip=True):
        return True
    return _PREMIUM_MARKER in meta_get(idx, "og:title", "twitter:title")


def _extract_status(soup: BeautifulSoup) -> str | None:
    """``Status`` label whose parent card holds the value (e.g. ``ongoing``)."""
    for el in soup.select(_STATUS_LABEL_SEL):
        if el.get_text(strip=True) == _STATUS_LABEL:
            parent = el.parent
            if parent is not None:
                text = parent.get_text(" ", strip=True)
                value = text[len(_STATUS_LABEL):].strip()
                if value:
                    return value
    return None


def _series_slug_from_url(url: str) -> str:
    parts = [p for p in url.rstrip("/").split("/") if p]
    try:
        i = parts.index("comics")
    except ValueError:
        return ""
    if i + 1 < len(parts):
        return parts[i + 1]
    return ""


def _chapter_number_from_slug(url: str) -> str | None:
    m = re.search(r"/comics/[^/]+/chapter/(\d+)", url.rstrip("/"))
    if m:
        return m.group(1)
    return None


def _extract_images(soup: BeautifulSoup) -> list[ImageItem]:
    images: list[ImageItem] = []
    seen: set[str] = set()

    for img in soup.find_all("img"):
        src = _attr_text(img.get("src")) or _attr_text(img.get("data-src"))
        if not src or src.startswith("data:"):
            continue
        host = (urlparse(src).hostname or "").lower()
        if host != _CDN_HOST or not any(
            mark in src for mark in _CHAPTER_PATH_MARKS
        ):
            continue

        clean = _clean_image_url(src)
        if clean in seen:
            continue
        seen.add(clean)
        images.append(ImageItem(url=clean, page_number=len(images) + 1))

    return images


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class AsurascansScraper(BaseScraper):
    """Asura Scans chapter and series scraper."""

    domain = DOMAIN
    name = "asurascans"

    def __init__(self) -> None:
        super().__init__()
        self._series_cache: dict[str, dict] = {}

    def matches_url(self, url: str) -> bool:
        return is_chapter_url(url) or is_series_url(url)

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        return await self._scrape_series(url, client)

    async def _series_page_data(self, series_slug: str, client: AsyncSession) -> dict:
        """Fetch the series page once per slug (cached on the instance).

        The chapter/reading page carries no genres/authors/artists/status/rating;
        those live only on the series page's ``ComicSeries`` JSON-LD. Best-effort
        — a failed fetch degrades to empty enrichment.
        """
        cached = self._series_cache.get(series_slug)
        if cached is not None:
            return cached
        data: dict = {}
        try:
            response = await BaseScraper._timeout_get(
                f"{BASE}/comics/{series_slug}", client
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            meta = _extract_meta(soup)
            data = {
                "series_title": meta.get("series_title", ""),
                "description": meta.get("description", ""),
                "cover_url": meta.get("cover_url", ""),
                "genres": meta.get("genres", []),
                "authors": meta.get("authors", []),
                "artists": meta.get("artists", []),
                "community_rating": meta.get("community_rating"),
                "status": _extract_status(soup),
            }
        # Enrichment is best-effort.
        except Exception:  # nosec B110
            pass
        self._series_cache[series_slug] = data
        return data

    async def _scrape_chapter(
        self, url: str, client: AsyncSession,
    ) -> ScrapedChapter:
        soup, _ = await self.fetch_html_raw(url, client)
        idx = meta_index(soup)

        if _is_premium_page(soup, idx):
            raise ScrapeError(
                "This chapter is premium/locked on Asura Scans and requires a "
                "paid account, which this tool does not support.",
                hint=f"Pick an unlocked chapter or drop the URL: {url}",
            )

        article = _article_node(soup)
        series_title = ""
        if article is not None:
            is_part_of = article.get("isPartOf")
            if isinstance(is_part_of, dict):
                series_title = _attr_text(is_part_of.get("name"))

        meta = _extract_meta(soup)
        if not series_title:
            series_title = meta.get("series_title", "")
        if not series_title:
            series_title = _extract_series_title(soup, idx)

        chapter_number = _chapter_number_from_slug(url)
        chapter_title = _extract_chapter_title(soup, idx, series_title)
        if not chapter_title or chapter_title == series_title:
            chapter_title = (
                f"Chapter {chapter_number}" if chapter_number else chapter_title or "Chapter"
            )

        images = _extract_images(soup)
        if not images:
            raise no_images_error()

        genres = meta.get("genres", [])
        authors = meta.get("authors", [])
        artists = meta.get("artists", [])
        status: str | None = None
        community_rating = meta.get("community_rating")
        description = meta.get("description", "")
        series_slug = _series_slug_from_url(url)
        if series_slug:
            series = await self._series_page_data(series_slug, client)
            genres = genres or series.get("genres", [])
            authors = authors or series.get("authors", [])
            artists = artists or series.get("artists", [])
            status = series.get("status")
            community_rating = community_rating or series.get("community_rating")
            description = description or series.get("description", "")

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=series_title or "Untitled",
                chapter_title=chapter_title or "Chapter",
                chapter_number=chapter_number,
                description=description,
                authors=authors,
                artists=artists,
                genres=genres,
                status=status,
                language=_extract_lang(soup),
                reading_direction="ltr",
                community_rating=community_rating,
                year=_extract_year(soup),
                total_pages=len(images),
            ),
            source=SourceInfo(url=url, service=DOMAIN),
            images=images,
            cover_url=_extract_cover(soup, idx),
        )

    async def _scrape_series(
        self, url: str, client: AsyncSession,
    ) -> SeriesMetadata:
        soup = await self.fetch_html(url, client)
        idx = meta_index(soup)
        meta = _extract_meta(soup)

        series_title = meta.get("series_title") or _extract_series_title(soup, idx)
        description = meta.get("description") or _extract_description(soup, idx)
        cover_url = meta.get("cover_url") or _extract_cover(soup, idx)
        title_no = _series_slug_from_url(url)

        chapters: list[dict] = []
        seen_urls: set[str] = set()

        for link in soup.select('a[href*="/chapter/"]'):
            href = _attr_text(link.get("href"))
            if not href or href in seen_urls:
                continue
            seen_urls.add(href)
            number = _chapter_number_from_slug(href)
            raw_title = link.get_text(strip=True) or ""
            title = f"Chapter {number}" if number else raw_title
            chapters.append({
                "title": title,
                "url": urljoin(url, href),
                "episode_no": number or title,
            })

        if not chapters:
            raise no_chapters_error()

        def _sort_key(item: dict) -> tuple[int, int]:
            num = item["episode_no"]
            if num.isdigit():
                return (0, int(num))
            return (1, 0)

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
