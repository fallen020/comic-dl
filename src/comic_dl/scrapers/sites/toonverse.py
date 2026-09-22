"""ToonVerse (toonverse.net) API-based chapter and series scraper.

The site is a client-rendered SPA (the HTML is an empty shell); all content
comes from the public JSON API at ``api.toonverse.net``. Anonymous GETs need
no auth: series detail resolves the slug to an id, chapters paginate by
``limit``/``offset``, and chapter pages carry direct CDN image URLs.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

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
    listing_page_error,
    no_chapters_error,
    no_images_error,
)
from ..registry import register_scraper

DOMAIN = "toonverse.net"
BASE = "https://toonverse.net"
_API = "https://api.toonverse.net/api"

_SERIES_PATH_RE = re.compile(r"^https?://(?:www\.)?toonverse\.net/series/[a-z0-9-]+/?$")
_CHAPTER_PATH_RE = re.compile(r"^https?://(?:www\.)?toonverse\.net/read/[a-z0-9-]+/\d+/?$")

_API_PAGE_SIZE = 50


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a ToonVerse series page."""
    return bool(_SERIES_PATH_RE.match(url))


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a ToonVerse reader page."""
    return bool(_CHAPTER_PATH_RE.match(url))


def _series_slug_from_url(url: str) -> str:
    parts = [p for p in urlparse(url).path.rstrip("/").split("/") if p]
    if len(parts) >= 2 and parts[0] == "series":
        return parts[1]
    if len(parts) >= 3 and parts[0] == "read":
        return parts[1]
    return ""


def _chapter_number_from_url(url: str) -> str | None:
    parts = [p for p in urlparse(url).path.rstrip("/").split("/") if p]
    if len(parts) >= 3 and parts[0] == "read" and parts[2].isdigit():
        return str(int(parts[2]))
    return None


def _episode_no(number: object) -> str | None:
    """Chapter number as a plain string (``1``, not ``1.0``)."""
    if isinstance(number, bool):
        return None
    if isinstance(number, int):
        return str(number)
    if isinstance(number, float):
        return str(int(number)) if number.is_integer() else str(number)
    if isinstance(number, str) and number.strip().isdigit():
        return str(int(number.strip()))
    return None


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class ToonVerseScraper(BaseScraper):
    """ToonVerse chapter and series scraper (JSON API transport)."""

    domain = DOMAIN
    name = "toonverse"
    site_id = "toonverse"
    version = "1.0.0"
    minimum_core_version = "0.0.2"

    def matches_url(self, url: str) -> bool:
        return is_chapter_url(url) or is_series_url(url)

    def matches_series_url(self, url: str) -> bool:
        return is_series_url(url)

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        return await self._scrape_series(url, client)

    async def _api_data(self, url: str, client: AsyncSession) -> dict:
        """``data`` payload of a ToonVerse API response (``{}`` when unshaped)."""
        resp = await BaseScraper._timeout_get(url, client, expect_json=True)
        resp.raise_for_status()
        try:
            body = resp.json()
        except ValueError:
            return {}
        if not isinstance(body, dict):
            return {}
        data = body.get("data", body)
        return data if isinstance(data, dict) else {}

    async def _series_detail(self, slug: str, client: AsyncSession) -> dict:
        return await self._api_data(f"{_API}/series/slug/{slug}", client)

    async def _scrape_chapter(self, url: str, client: AsyncSession) -> ScrapedChapter:
        if not is_chapter_url(url):
            raise listing_page_error("ToonVerse", f"{BASE}/series/{{slug}}/")
        slug = _series_slug_from_url(url)
        number = _chapter_number_from_url(url)
        if not slug or not number:
            raise no_images_error()
        data = await self._api_data(f"{_API}/reading/chapter/{slug}/{number}", client)
        chapter = data.get("chapter", {}) if isinstance(data, dict) else {}
        pages = chapter.get("pages", []) if isinstance(chapter, dict) else []
        if not isinstance(pages, list) or not pages:
            raise no_images_error()

        images: list[ImageItem] = []
        for page in pages:
            if not isinstance(page, dict) or not page.get("imageUrl"):
                continue
            page_number = len(images) + 1
            n = page.get("number")
            if isinstance(n, bool):
                pass
            elif isinstance(n, int):
                page_number = n
            elif isinstance(n, float) and n.is_integer():
                page_number = int(n)
            item = ImageItem.from_url(str(page["imageUrl"]), page_number)
            if item is not None:
                images.append(item)
        if not images:
            raise no_images_error()
        images.sort(key=lambda i: i.page_number)

        series = data.get("series", {}) if isinstance(data, dict) else {}
        series_title = (
            str(series.get("title") or "") if isinstance(series, dict) else ""
        ) or slug.replace("-", " ").title()
        chapter_title = (
            str(chapter.get("title") or "") if isinstance(chapter, dict) else ""
        ) or f"Chapter {number}"

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=series_title,
                chapter_title=chapter_title,
                chapter_number=number,
                language="en",
                reading_direction="ltr",
                total_pages=len(images),
            ),
            source=SourceInfo(url=url, service=DOMAIN, post_id=f"{slug}/{number}"),
            images=images,
            cover_url="",
        )

    async def _scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        if not is_series_url(url):
            raise listing_page_error("ToonVerse", f"{BASE}/series/{{slug}}/")
        slug = _series_slug_from_url(url)
        if not slug:
            raise listing_page_error("ToonVerse", f"{BASE}/series/{{slug}}/")
        detail = await self._series_detail(slug, client)
        series_id = detail.get("id")
        if not series_id:
            raise no_chapters_error()

        chapters: list[dict] = []
        offset = 0
        total: int | None = None
        while True:
            data = await self._api_data(
                f"{_API}/series/{series_id}/chapters"
                f"?limit={_API_PAGE_SIZE}&offset={offset}&order=asc",
                client,
            )
            page = data.get("chapters", [])
            if total is None and isinstance(data.get("total"), int):
                total = data["total"]
            if not isinstance(page, list) or not page:
                break
            for entry in page:
                if not isinstance(entry, dict):
                    continue
                episode_no = _episode_no(entry.get("number"))
                if episode_no is None:
                    continue
                title = str(entry.get("title") or "").strip() or f"Chapter {episode_no}"
                chapters.append(
                    {
                        "title": title,
                        "url": f"{BASE}/read/{slug}/{episode_no}",
                        "episode_no": episode_no,
                    }
                )
            offset += len(page)
            if len(page) < _API_PAGE_SIZE:
                break
            if total is not None and len(chapters) >= total:
                break

        if not chapters:
            raise no_chapters_error()

        return SeriesMetadata(
            series_title=str(detail.get("title") or slug.replace("-", " ").title()),
            description=str(detail.get("description") or ""),
            cover_url=str(detail.get("coverUrl") or ""),
            title_no=slug,
            chapters=chapters,
        )
