"""WeebCentral scraper (weebcentral.com series + chapter pages)."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

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
from ...utils import sanitize_filename
from ..base import BaseScraper, _attr_text, meta_get, meta_index
from ..registry import register_scraper

DOMAIN = "weebcentral.com"

_CHAPTER_PATH_RE = re.compile(r"^/chapters/([A-Za-z0-9_-]+)/?$")
_SERIES_PATH_RE = re.compile(r"^/series/([A-Za-z0-9_-]+)(?:/[^/]*)?/?$")
# A real series link carries a slug segment ("/series/{id}/{slug}"); the site
# nav also links "/series/random", which must never win as the series.
_SERIES_SLUG_PATH_RE = re.compile(r"^/series/([A-Za-z0-9_-]+)/[^/]+/?$")
_CHAPTER_LABEL_RE = re.compile(r"^(.*?)\s+([\d.]+)$")


def is_chapter_url(url: str) -> bool:
    """True for ``/chapters/{id}`` reader URLs."""
    return _CHAPTER_PATH_RE.match(urlsplit(url).path or "") is not None


def is_series_url(url: str) -> bool:
    """True for ``/series/{id}[/{slug}]`` series URLs."""
    return _SERIES_PATH_RE.match(urlsplit(url).path or "") is not None


def _split_chapter_label(label: str) -> tuple[str, str | None]:
    """Split ``"Navigation 67.5"`` into title + number (``None`` when bare)."""
    match = _CHAPTER_LABEL_RE.match(label.strip())
    if match:
        return match.group(1).strip() or label.strip(), match.group(2)
    return label.strip(), None


def _extract_images(soup: BeautifulSoup) -> list[ImageItem]:
    """Page images from the ``/images`` fragment, in document order.

    Skips the ``broken_image`` placeholder the reader swaps in for failed
    loads, plus anything that is not an absolute http(s) URL.
    """
    items: list[ImageItem] = []
    seen: set[str] = set()
    for img in soup.select("section#chapter-images img"):
        src = _attr_text(img.get("src"))
        if not src or src in seen:
            continue
        if not src.startswith(("http://", "https://")):
            continue
        if "broken_image" in src:
            continue
        seen.add(src)
        items.append(ImageItem(url=src, page_number=len(items) + 1))
    return items


def _extract_chapter_context(
    soup: BeautifulSoup, idx: dict[str, list[str]]
) -> tuple[str, str, str]:
    """Return ``(series_title, series_url, chapter_label)`` for a chapter page.

    The series link is embedded in the reader nav; the ``og:title`` tag reads
    ``"<chapter> | <series> | Weeb Central"``. Either may be absent, so each
    has a fallback and the label degrades to the chapter-select button text.
    """
    series_url = ""
    series_title = ""
    for link in soup.select('a[href*="/series/"]'):
        href = _attr_text(link.get("href"))
        if _SERIES_SLUG_PATH_RE.match(href):
            series_url = f"https://{DOMAIN}{href}"
            series_title = link.get_text(strip=True)
            break

    chapter_label = ""
    og_title = meta_get(idx, "og:title")
    if og_title:
        parts = [p.strip() for p in og_title.split("|")]
        if len(parts) >= 2:
            chapter_label = parts[0]
            if not series_title:
                series_title = parts[1]
    if not chapter_label:
        button = soup.select_one("button span")
        if button is not None:
            chapter_label = button.get_text(strip=True)
    return series_title, series_url, chapter_label


def _extract_series_meta(
    soup: BeautifulSoup, idx: dict[str, list[str]]
) -> tuple[str, str, str]:
    """Return ``(title, description, cover_url)`` for a series page."""
    title = ""
    og_title = meta_get(idx, "og:title")
    if og_title:
        title = og_title.split("|")[0].strip()
    if not title:
        heading = soup.select_one("h1")
        if heading is not None:
            title = heading.get_text(strip=True)
    description = meta_get(idx, "og:description")
    cover_url = meta_get(idx, "og:image")
    return title, description, cover_url


def _extract_chapter_list(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Return ``(label, url)`` pairs from a ``full-chapter-list`` fragment.

    The fragment lists newest-first; callers reverse to ascending.
    """
    entries: list[tuple[str, str]] = []
    for link in soup.select('a[href^="/chapters/"]'):
        href = _attr_text(link.get("href"))
        label = link.get_text(" ", strip=True)
        if not href or not label or (label, href) in entries:
            continue
        entries.append((label, f"https://{DOMAIN}{href}"))
    return entries


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class WeebCentralScraper(BaseScraper):
    """WeebCentral scraper (server-rendered HTML + HTMX image fragments)."""

    domain = DOMAIN
    name = "weebcentral"

    def matches_url(self, url: str) -> bool:
        return is_chapter_url(url) or is_series_url(url)

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def _scrape_chapter(
        self, url: str, client: AsyncSession,
    ) -> ScrapedChapter:
        soup = await self.fetch_html(url, client)
        idx = meta_index(soup)
        series_title, _series_url, chapter_label = _extract_chapter_context(soup, idx)
        if not chapter_label:
            raise ScrapeError(
                "Could not find the chapter title on this WeebCentral page.",
                hint="The page layout may have changed.",
            )

        images_url = url.rstrip("/") + "/images?reading_style=long_strip"
        images = _extract_images(await self.fetch_html(images_url, client))
        if not images:
            raise ScrapeError(
                "No images found for this WeebCentral chapter — it may be "
                "locked or require login.",
            )

        chapter_title, chapter_number = _split_chapter_label(chapter_label)
        if not series_title:
            series_title = chapter_title
        cover_url = meta_get(idx, "og:image")
        chapter_id = _CHAPTER_PATH_RE.match(urlsplit(url).path or "")
        return ScrapedChapter(
            info=ChapterInfo(
                series_title=sanitize_filename(series_title),
                chapter_title=sanitize_filename(chapter_label),
                chapter_number=chapter_number,
            ),
            source=SourceInfo(
                url=url,
                service=DOMAIN,
                post_id=chapter_id.group(1) if chapter_id else "",
            ),
            images=images,
            cover_url=cover_url,
        )

    async def scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        return await self._scrape_series(url, client)

    async def _scrape_series(
        self, url: str, client: AsyncSession,
    ) -> SeriesMetadata:
        soup = await self.fetch_html(url, client)
        idx = meta_index(soup)
        series_title, description, cover_url = _extract_series_meta(soup, idx)
        if not series_title:
            raise ScrapeError(
                "Could not find series metadata on this WeebCentral page.",
                hint="The page layout may have changed.",
            )

        match = _SERIES_PATH_RE.match(urlsplit(url).path or "")
        series_id = match.group(1) if match else ""
        list_url = f"https://{DOMAIN}/series/{series_id}/full-chapter-list"
        entries = _extract_chapter_list(await self.fetch_html(list_url, client))
        if not entries:
            raise ScrapeError(
                "No chapters found for this WeebCentral series.",
                hint="The series may have no published chapters yet.",
            )

        chapters = []
        for label, chapter_url in reversed(entries):
            _title, number = _split_chapter_label(label)
            chapters.append(
                {"title": label, "episode_no": number or label, "url": chapter_url}
            )
        return SeriesMetadata(
            series_title=sanitize_filename(series_title),
            chapters=chapters,
            description=description,
            cover_url=cover_url,
        )


async def scrape_chapter(url: str, client: AsyncSession) -> PostMetadata:
    """Scrape a WeebCentral chapter through a fresh scraper (test helper)."""
    scraper = WeebCentralScraper()
    return await scraper.scrape(url, client)
