"""StoneScape (stonescape.xyz) scraper for its Vue SPA JSON API.

The site renders no server-side HTML — pages ship an empty ``#app`` shell and
the reader is fully client-rendered. All public data (series detail, chapter
lists, page images) comes from a plain JSON REST API under ``/api/...`` that
answers ordinary HTTP GET requests, so the scraper needs no headless browser.
Public page images are served from the same origin under ``/pub/...``.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

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
from ..base import BaseScraper, no_chapters_error, no_images_error
from ..registry import register_scraper

DOMAIN = "stonescape.xyz"
BASE = "https://stonescape.xyz"

_SERIES_PATH_RE = re.compile(r"^https?://(?:www\.)?stonescape\.xyz/series/[^/]+/?$")

_CHAPTER_PATH_RE = re.compile(
    r"^https?://(?:www\.)?stonescape\.xyz/series/[^/]+/ch-\d+(?:\.\d+)?/?$"
)

_CHAPTER_NUM_RE = re.compile(r"/ch-(\d+(?:\.\d+)?)/?$")

_PUB_PATH = "/pub/"


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a series page for this source."""
    return bool(_SERIES_PATH_RE.match(url))


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a chapter reader page (``/ch-<number>``)."""
    return bool(_CHAPTER_PATH_RE.match(url))


def _normalize_number(raw: str) -> str:
    """Collapse ``"36.00"`` to ``"36"`` (and ``"5.50"`` to ``"5.5"``).

    Mirrors the site's reader URL format: chapter numbers are stored with
    trailing zeroes but the SPA routes them as ``ch-36``.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return str(raw)
    return format(value, ".15g")


def _absolute(raw: str) -> str:
    """Resolve an origin-relative API URL (``/pub/...``) to an absolute one."""
    if raw.startswith(("http://", "https://")):
        return raw
    return urljoin(BASE, raw)


def _slug_from_url(url: str) -> str:
    parts = [p for p in url.rstrip("/").split("/") if p]
    try:
        i = parts.index("series")
    except ValueError:
        return ""
    return parts[i + 1] if i + 1 < len(parts) else ""


def _chapter_number_from_url(url: str) -> str | None:
    m = _CHAPTER_NUM_RE.search(url.rstrip("/"))
    return m.group(1) if m else None


def _find_chapter(chapters: object, wanted: str) -> dict | None:
    """First chapter record whose number normalizes to ``wanted``."""
    if not isinstance(chapters, list):
        return None
    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        raw = chapter.get("chapterNumber")
        if raw is None:
            continue
        if _normalize_number(str(raw)) == wanted:
            return chapter
    return None


def _page_images(payload: dict) -> list[ImageItem]:
    """Public page records as ordered, deduplicated image items.

    Only ``delivery: "public"`` pages are usable: protected delivery serves
    encrypted content the reader decodes in JS, which a plain HTTP client
    cannot read. Page URLs are origin-relative and must live under ``/pub/``.
    """
    images: list[ImageItem] = []
    seen: set[str] = set()

    for page in payload.get("pages", []):
        if not isinstance(page, dict) or page.get("delivery") != "public":
            continue
        raw = page.get("url")
        if not isinstance(raw, str) or not raw:
            continue
        url = _absolute(raw)
        parsed = urlparse(url)
        if (parsed.hostname or "").lower() != DOMAIN:
            continue
        if not parsed.path.startswith(_PUB_PATH):
            continue
        if url in seen:
            continue
        seen.add(url)
        raw_number = page.get("pageNumber")
        try:
            page_number = int(raw_number) if raw_number is not None else len(images) + 1
        except (TypeError, ValueError):
            page_number = len(images) + 1
        images.append(ImageItem(url=url, page_number=page_number))

    images.sort(key=lambda img: (img.page_number, img.url))
    return images


def _chapter_sort_key(item: dict):
    try:
        return float(item["episode_no"])
    except (TypeError, ValueError, KeyError):
        return float("inf")


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class StoneScapeScraper(BaseScraper):
    """StoneScape chapter and series scraper (JSON API)."""

    domain = DOMAIN
    name = "stonescape"
    site_id = "stonescape"
    version = "1.0.0"
    minimum_core_version = "0.0.2"

    def matches_url(self, url: str) -> bool:
        return is_chapter_url(url) or is_series_url(url)

    def matches_series_url(self, url: str) -> bool:
        return is_series_url(url)

    @staticmethod
    async def _fetch_json(url: str, client: AsyncSession) -> dict:
        """Fetch JSON through the safe HTTP path, mapping API failures."""
        try:
            response = await BaseScraper._timeout_get(url, client)
            response.raise_for_status()
        except CurlHTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 404:
                raise ScrapeError(
                    "Not found on StoneScape.",
                    hint="the series or chapter may have been removed, or this link is dead.",
                ) from None
            if status == 403:
                raise ScrapeError(
                    "Chapter is locked on StoneScape.",
                    hint="locked chapters need coins or a subscription, which "
                    "this tool does not pay.",
                ) from None
            raise
        data = response.json()
        if not isinstance(data, dict):
            raise ScrapeError(
                "Unexpected response from StoneScape.",
                hint="the site's API shape may have changed.",
            )
        return data

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        return await self._scrape_series(url, client)

    async def _scrape_chapter(
        self,
        url: str,
        client: AsyncSession,
    ) -> ScrapedChapter:
        slug = _slug_from_url(url)
        number = _normalize_number(_chapter_number_from_url(url) or "")

        payload = await self._fetch_json(f"{BASE}/api/series/by-slug/{slug}/chapters", client)
        chapter = _find_chapter(payload.get("chapters"), number)
        if chapter is None:
            raise ScrapeError(
                f"Chapter {number} not found on StoneScape.",
                hint="the chapter may be unreleased or removed.",
            )
        if chapter.get("locked"):
            raise ScrapeError(
                "Chapter is locked on StoneScape.",
                hint="locked chapters need coins or a subscription, which this tool does not pay.",
            )

        detail = await self._fetch_json(f"{BASE}/api/series/by-slug/{slug}", client)
        pages = await self._fetch_json(f"{BASE}/api/chapters/{chapter['chapterId']}/pages", client)
        images = _page_images(pages)
        if not images:
            raise no_images_error()

        raw_title = chapter.get("title")
        chapter_title = str(raw_title) if raw_title else f"Chapter {number}"

        series_title = str(detail.get("title") or "Untitled")
        authors = [
            str(value)
            for value in (detail.get("author"), detail.get("artist"))
            if isinstance(value, str) and value.strip()
        ]
        genres = [
            str(genre)
            for genre in detail.get("genres", [])
            if isinstance(genre, str) and genre.strip()
        ]
        status = detail.get("publicationStatus")
        if isinstance(status, str):
            status = status or None
        rating = detail.get("averageRating")
        community_rating = float(rating) if isinstance(rating, (int, float)) else None

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=series_title,
                chapter_title=chapter_title,
                chapter_number=number,
                description=str(detail.get("description") or ""),
                total_pages=len(images),
                authors=authors,
                genres=genres,
                status=status,
                community_rating=community_rating,
            ),
            source=SourceInfo(url=url, service=DOMAIN),
            images=images,
            cover_url=_absolute(str(detail.get("coverUrl") or "")),
        )

    async def _scrape_series(
        self,
        url: str,
        client: AsyncSession,
    ) -> SeriesMetadata:
        slug = _slug_from_url(url)

        detail = await self._fetch_json(f"{BASE}/api/series/by-slug/{slug}", client)
        payload = await self._fetch_json(f"{BASE}/api/series/by-slug/{slug}/chapters", client)

        entries: list[dict] = []
        for chapter in payload.get("chapters", []):
            if not isinstance(chapter, dict):
                continue
            raw = chapter.get("chapterNumber")
            if raw is None:
                continue
            num = _normalize_number(str(raw))
            title = chapter.get("title")
            entries.append(
                {
                    "title": str(title) if title else f"Chapter {num}",
                    "url": f"{BASE}/series/{slug}/ch-{num}",
                    "episode_no": num,
                }
            )

        if not entries:
            raise no_chapters_error()

        entries.sort(key=_chapter_sort_key)

        series_title = str(detail.get("title") or "").strip()
        if not series_title:
            series_title = slug.replace("-", " ").title() if slug else "Untitled"

        return SeriesMetadata(
            series_title=series_title,
            description=str(detail.get("description") or ""),
            cover_url=_absolute(str(detail.get("coverUrl") or "")),
            title_no=slug,
            chapters=entries,
        )
