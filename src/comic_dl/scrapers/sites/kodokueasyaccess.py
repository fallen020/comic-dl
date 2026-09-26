"""KodokuEasyAccess (kodokueasyaccess.com) API-based chapter and series scraper.

The site is a client-rendered SPA behind an 18+ age gate, so the HTML is an
empty shell; everything comes from the public JSON API under ``/api``. The gate
is client-side only: the API answers anonymous GETs with no token, no cookie,
and no consent flag, so a plain fetch is enough.

One series is published, ``Reverend Insanity``, translated into several
languages. A chapter is addressed by ``/read/{series}/{lang}/{number}``, and the
chapter list mixes every language together, so a scrape has to pick one —
``DEFAULT_LANGUAGE`` is used for the listing and any chapter URL may name its own.

New chapters are released on a schedule: the list marks each one ``available``
and the detail endpoint answers a not-yet-released chapter with the same flag
plus an ``availableAt`` date instead of images. Unavailable chapters are left
out of the listing so ``--all-chapters`` does not walk into them.
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlparse

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
from ..base import BaseScraper, listing_page_error, no_chapters_error, no_images_error
from ..registry import register_scraper

DOMAIN = "kodokueasyaccess.com"
BASE = "https://kodokueasyaccess.com"

# The listing has no language input of its own, and English is the track the
# site's own reader links to first.
DEFAULT_LANGUAGE = "en"

_SERIES_PATH_RE = re.compile(
    r"^https?://(?:www\.)?kodokueasyaccess\.com/manhwa/([a-z0-9][a-z0-9-]*)/?$",
    re.IGNORECASE,
)
_CHAPTER_PATH_RE = re.compile(
    r"^https?://(?:www\.)?kodokueasyaccess\.com/read/([a-z0-9][a-z0-9-]*)/([a-z]{2,5})/(\d+)/?$",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a series page for this source."""
    return bool(_SERIES_PATH_RE.match(url))


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a chapter/reader page for this source."""
    return bool(_CHAPTER_PATH_RE.match(url))


def _series_slug_from_url(url: str) -> str:
    parts = [p for p in urlparse(url).path.rstrip("/").split("/") if p]
    if len(parts) >= 2 and parts[0].lower() == "manhwa":
        return parts[1]
    if len(parts) >= 2 and parts[0].lower() == "read":
        return parts[1]
    return ""


def _chapter_ref_from_url(url: str) -> tuple[str, str, str]:
    """``(series slug, language code, chapter number)`` from a reader URL."""
    match = _CHAPTER_PATH_RE.match(url)
    if match is None:
        return "", "", ""
    slug, language, number = match.groups()
    return slug, language.lower(), str(int(number))


def _title_from_slug(slug: str) -> str:
    return slug.replace("-", " ").strip().title()


def _unlock_date(payload: dict) -> str:
    """The ``availableAt`` date of a locked chapter, as ``YYYY-MM-DD``."""
    raw = payload.get("availableAt")
    if not isinstance(raw, str):
        return ""
    match = _ISO_DATE_RE.match(raw.strip())
    return match.group(0) if match else ""


def _chapter_sort_key(entry: dict) -> tuple[int, str]:
    number = str(entry.get("chapterNumber") or "")
    return (int(number) if number.isdigit() else 0, number)


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class KodokuEasyAccessScraper(BaseScraper):
    """KodokuEasyAccess chapter and series scraper (JSON API transport)."""

    domain = DOMAIN
    name = "kodokueasyaccess"
    site_id = "kodokueasyaccess"
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

    async def _api_json(self, path: str, client: AsyncSession) -> object:
        """GET a JSON API path and return the decoded body (``None`` if not JSON).

        Never cached. Every payload here is time-sensitive: cover and page URLs
        are presigned and expire after 30 minutes, and a chapter's ``available``
        flag flips on its release schedule. A cache entry older than that hands
        back image links that 403, so freshness is worth more than the hit.
        """
        resp = await BaseScraper._timeout_get(f"{BASE}{path}", client, use_cache=False)
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return None

    async def _scrape_chapter(self, url: str, client: AsyncSession) -> ScrapedChapter:
        if not is_chapter_url(url):
            raise listing_page_error("Kodoku", f"{BASE}/manhwa/{{slug}}/")
        slug, language, number = _chapter_ref_from_url(url)
        if not slug or not number:
            raise no_images_error()

        payload = await self._api_json(
            f"/api/series/{quote(slug, safe='')}/chapters/{number}/{quote(language, safe='')}",
            client,
        )
        if not isinstance(payload, dict):
            raise no_images_error()

        if payload.get("available") is False:
            when = _unlock_date(payload)
            hint = "New chapters unlock on a schedule; Patreon tiers get them earlier."
            if when:
                hint = f"Scheduled for {when}. Patreon tiers get new chapters earlier."
            raise ScrapeError(
                f"Chapter {number} ({language}) is not available yet.",
                hint=hint,
            )

        pages = payload.get("images")
        if not isinstance(pages, list) or not pages:
            raise no_images_error()

        images: list[ImageItem] = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            raw = page.get("url")
            if not raw:
                continue
            sequence = page.get("sequence")
            if isinstance(sequence, int) and not isinstance(sequence, bool):
                page_number = sequence + 1
            else:
                page_number = len(images) + 1
            item = ImageItem.from_url(str(raw), page_number)
            if item is not None:
                images.append(item)
        if not images:
            raise no_images_error()
        images.sort(key=lambda i: i.page_number)

        # The chapter payload names no series title, and its slug is the title
        # in slug form, so derive it rather than spend a request per chapter.
        title = payload.get("title")
        chapter_title = str(title).strip() if title else ""

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=_title_from_slug(slug),
                chapter_title=chapter_title or f"Chapter {number}",
                chapter_number=number,
                language=language,
                reading_direction="ltr",
                total_pages=len(images),
            ),
            source=SourceInfo(url=url, service=DOMAIN, post_id=f"{slug}/{language}/{number}"),
            images=images,
            cover_url="",
        )

    async def _scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        if not is_series_url(url):
            raise listing_page_error("Kodoku", f"{BASE}/manhwa/{{slug}}/")
        slug = _series_slug_from_url(url)
        if not slug:
            raise listing_page_error("Kodoku", f"{BASE}/manhwa/{{slug}}/")
        quoted = quote(slug, safe="")

        detail = await self._api_json(f"/api/series/{quoted}", client)
        if not isinstance(detail, dict):
            detail = {}

        listed = await self._api_json(f"/api/series/{quoted}/chapters", client)
        if not isinstance(listed, list):
            raise no_chapters_error()

        entries = [
            entry
            for entry in listed
            if isinstance(entry, dict)
            and entry.get("available") is True
            and str(entry.get("languageCode") or "").lower() == DEFAULT_LANGUAGE
            and str(entry.get("chapterNumber") or "").strip()
        ]
        if not entries:
            raise no_chapters_error()
        entries.sort(key=_chapter_sort_key)

        chapters: list[dict] = []
        for entry in entries:
            number = str(entry["chapterNumber"]).strip()
            title = str(entry.get("title") or "").strip()
            chapters.append(
                {
                    "title": title or f"Chapter {number}",
                    "url": f"{BASE}/read/{slug}/{DEFAULT_LANGUAGE}/{number}",
                    "episode_no": number,
                }
            )

        description = str(detail.get("description") or "").strip()
        return SeriesMetadata(
            series_title=str(detail.get("title") or "").strip() or _title_from_slug(slug),
            description=description,
            cover_url=str(detail.get("coverUrl") or ""),
            title_no=slug,
            chapters=chapters,
        )
