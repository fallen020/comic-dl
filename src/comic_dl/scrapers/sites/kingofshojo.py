"""Kingofshojo (kingofshojo.com) scraper for its Madara-style reader.

Chapter-only support: series pages load chapter lists dynamically via JavaScript
and cannot be scraped statically. Chapter pages work normally.
"""

from __future__ import annotations

import re
import urllib.parse

from bs4 import BeautifulSoup

from ...models import (
    ChapterInfo,
    ImageItem,
    PostMetadata,
    ScrapedChapter,
    SeriesMetadata,
    SourceInfo,
    chapter_to_post_metadata,
)
from ...utils import sanitize_filename
from ..base import (
    BaseScraper,
    _attr_text,
    meta_get,
    meta_index,
    no_images_error,
)
from ..registry import register_scraper

DOMAIN = "kingofshojo.com"
BASE = "https://kingofshojo.com"

# Chapters are at flat root: /{slug}-chapter-{n}/
_CHAPTER_PATH_RE = re.compile(
    r"^https?://(?:www\.)?kingofshojo\.com/[^/]+-chapter-\d+(?:\.\d+)?/?$"
)

_CHAPTER_NUM_RE = re.compile(r"-chapter-(\d+(?:\.\d+)?)/?$", re.IGNORECASE)

_READ_CONTAINERS = ("#readerarea",)


def is_series_url(url: str) -> bool:
    """Kingofshojo series pages not supported (dynamic chapter list)."""
    return False


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a chapter/gallery page for this source."""
    return bool(_CHAPTER_PATH_RE.match(url))


def _on_image_host(raw: str) -> bool:
    host = urllib.parse.urlparse(raw).hostname or ""
    host = host.lower()
    return (
        host == DOMAIN
        or host.startswith("cdn.")
        or host.endswith(".wp.com")
        or host == "i.ibb.co"
    )


def _extract_series_slug(url: str) -> str:
    """Extract series slug from chapter URL."""
    parts = [p for p in url.rstrip("/").split("/") if p]
    if parts:
        slug_part = parts[-1]
        if "-chapter-" in slug_part:
            return slug_part.split("-chapter-")[0]
    return ""


def _chapter_number_from_url(url: str) -> str | None:
    m = _CHAPTER_NUM_RE.search(url)
    return m.group(1) if m else None


def _extract_series_title(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    """Series title from chapter page OpenGraph/meta tags."""
    page_title = meta_get(idx, "og:title", "twitter:title")
    if not page_title:
        title_tag = soup.select_one("title")
        page_title = title_tag.get_text(strip=True) if title_tag else ""
    parts = [p.strip() for p in page_title.split(" - ") if p.strip()]
    if parts and parts[-1].lower().startswith("kingofshojo"):
        parts = parts[:-1]
    if parts:
        # Take first part as series title
        title = parts[0]
        for term in (" Chapter ", " Manga ", " Read "):
            title = title.replace(term, "")
        return title.strip()
    return ""


def _extract_images(soup: BeautifulSoup) -> list[ImageItem]:
    images: list[ImageItem] = []
    seen: set[str] = set()

    scope = None
    for sel in _READ_CONTAINERS:
        scope = soup.select_one(sel)
        if scope is not None:
            break
    if scope is None:
        scope = soup

    for img in scope.find_all("img"):
        src = ""
        for attr in ("data-src", "data-lazy-src", "src"):
            candidate = _attr_text(img.get(attr))
            if candidate:
                src = candidate
                break
        if not src or src.startswith("data:"):
            continue
        if not _on_image_host(src):
            continue

        clean = src.split("?")[0].split("#")[0]
        if clean in seen:
            continue
        seen.add(clean)
        images.append(ImageItem(url=clean, page_number=len(images) + 1))

    return images


@register_scraper(domain=DOMAIN, capabilities={"chapter"})
class KingofshojoScraper(BaseScraper):
    """Kingofshojo chapter scraper (series not supported)."""

    domain = DOMAIN
    name = "kingofshojo"
    base_url = BASE

    chapter_url_re = _CHAPTER_PATH_RE

    def matches_url(self, url: str) -> bool:
        return is_chapter_url(url)

    async def scrape(self, url: str, client) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def scrape_series(self, url: str, client) -> SeriesMetadata:
        raise NotImplementedError(
            "Kingofshojo series scraping not supported (dynamic chapter list)"
        )

    async def _scrape_chapter(self, url: str, client) -> ScrapedChapter:
        soup, _ = await self.fetch_html_raw(url, client)

        images = _extract_images(soup)
        if not images:
            raise no_images_error()

        idx = meta_index(soup)
        series_title = _extract_series_title(soup, idx)
        h1 = soup.select_one("h1")
        chapter_title = h1.get_text(strip=True) if h1 else "Chapter"

        info = {
            "series_title": sanitize_filename(series_title) or "Untitled",
            "chapter_title": sanitize_filename(chapter_title) or "Chapter",
            "chapter_number": _chapter_number_from_url(url),
            "language": "en",
            "reading_direction": "ltr",
            "total_pages": len(images),
            "authors": [],
            "genres": [],
            "status": None,
            "description": "",
        }

        return ScrapedChapter(
            info=ChapterInfo(**info),
            source=SourceInfo(url=url, service=DOMAIN, post_id=""),
            images=images,
            cover_url="",
        )
