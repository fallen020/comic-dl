"""MangaDex (mangadex.org) scraper backed by the public REST API."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

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
from ..base import BaseScraper, no_chapters_error, no_images_error
from ..registry import register_scraper

DOMAIN = "mangadex.org"
BASE = "https://mangadex.org"
_API = "https://api.mangadex.org"
_UPLOADS = "https://uploads.mangadex.org"

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

_SERIES_PATH_RE = re.compile(
    rf"^https?://(?:www\.)?mangadex\.org/(?:title|manga)/{_UUID}/?$",
    re.IGNORECASE,
)
_CHAPTER_PATH_RE = re.compile(
    rf"^https?://(?:www\.)?mangadex\.org/chapter/{_UUID}/?$",
    re.IGNORECASE,
)

# Anonymous API limits are 5 requests/second per IP; stay just under and let
# the per-host token bucket pace the scrape path.
_FEED_LIMIT = 500
_FEED_PAGE_CAP = 20
_CONTENT_RATINGS = ("safe", "suggestive", "erotica", "pornographic")


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a series page for this source."""
    return bool(_SERIES_PATH_RE.match(url))


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a chapter/gallery page for this source."""
    return bool(_CHAPTER_PATH_RE.match(url))


def _uuid_at(path_parts: list[str], *labels: str) -> str | None:
    """The first UUID following any of ``labels`` in a URL path, if any."""
    for label in labels:
        for i, part in enumerate(path_parts):
            if part == label and i + 1 < len(path_parts):
                candidate = path_parts[i + 1]
                if re.fullmatch(_UUID, candidate, re.IGNORECASE):
                    return candidate
    return None


def _extract_series_id(url: str) -> str | None:
    return _uuid_at([p for p in urlparse(url).path.split("/") if p], "title", "manga")


def _extract_chapter_id(url: str) -> str | None:
    return _uuid_at([p for p in urlparse(url).path.split("/") if p], "chapter")


def _title_of(titles: object) -> str:
    """Best-effort title from a MangaDex ``{locale: title}`` mapping."""
    if not isinstance(titles, dict):
        return ""
    for preferred in ("en", "ja-ro", "ja", "ko", "zh"):
        value = titles.get(preferred)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for value in titles.values():
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _description_of(descriptions: object) -> str:
    if not isinstance(descriptions, dict):
        return ""
    for preferred in ("en",):
        value = descriptions.get(preferred)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for value in descriptions.values():
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _relationship_ids(data: object, rel_type: str) -> list[str]:
    relationships = data.get("relationships", []) if isinstance(data, dict) else []
    return [
        str(rel.get("id"))
        for rel in relationships
        if isinstance(rel, dict) and rel.get("type") == rel_type and rel.get("id")
    ]


def _relationship_names(data: object, rel_type: str) -> list[str]:
    relationships = data.get("relationships", []) if isinstance(data, dict) else []
    out: list[str] = []
    for rel in relationships:
        if not isinstance(rel, dict) or rel.get("type") != rel_type:
            continue
        attributes = rel.get("attributes")
        name = attributes.get("name") if isinstance(attributes, dict) else None
        if isinstance(name, str) and name.strip():
            out.append(name.strip())
    return out


def _genre_names(data: object) -> list[str]:
    tags = data.get("attributes", {}).get("tags", []) if isinstance(data, dict) else []
    out: list[str] = []
    for tag in tags:
        if not isinstance(tag, dict):
            continue
        if tag.get("attributes", {}).get("group") != "genre":
            continue
        name = _title_of(tag.get("attributes", {}).get("name"))
        if name:
            out.append(name)
    return out


def _cover_filename(data: object) -> str:
    for rel in data.get("relationships", []) if isinstance(data, dict) else []:
        if not isinstance(rel, dict) or rel.get("type") != "cover_art":
            continue
        attributes = rel.get("attributes")
        filename = (
            attributes.get("fileName") if isinstance(attributes, dict) else None
        )
        if isinstance(filename, str) and filename.strip():
            return filename.strip()
    return ""


def _manga_fields(data: object, manga_id: str) -> dict:
    """Flat best-effort metadata shared by the chapter and series models."""
    attributes = data.get("attributes", {}) if isinstance(data, dict) else {}
    rating = attributes.get("rating")
    community_rating = float(rating) if isinstance(rating, (int, float)) else None
    year = attributes.get("year")
    year = int(year) if isinstance(year, (int, float)) else None
    cover_filename = _cover_filename(data)
    cover_url = (
        f"{_UPLOADS}/covers/{manga_id}/{cover_filename}" if cover_filename else ""
    )
    return {
        "series_title": _title_of(attributes.get("title")),
        "description": _description_of(attributes.get("description")),
        "genres": _genre_names(data),
        "authors": _relationship_names(data, "author"),
        "artists": _relationship_names(data, "artist"),
        "status": attributes.get("status") or None,
        "year": year,
        "community_rating": community_rating,
        "cover_url": cover_url,
    }


def _feed_url(manga_id: str, offset: int) -> str:
    ratings = "&".join(f"contentRating[]={r}" for r in _CONTENT_RATINGS)
    return (
        f"{_API}/manga/{manga_id}/feed"
        f"?translatedLanguage[]=en&order[volume]=asc&order[chapter]=asc"
        f"&limit={_FEED_LIMIT}&offset={offset}&{ratings}"
    )


def _chapter_sort_key(raw: object) -> tuple:
    """Numeric-then-lexicographic key so ``1.5 < 2`` and names sort last."""
    if raw is None:
        return (2, 0.0, "")
    value = str(raw).strip()
    try:
        return (0, float(value), "")
    except ValueError:
        return (1, 0.0, value.lower())


def _page_files(server_data: object) -> list[str]:
    chapter = server_data.get("chapter", {}) if isinstance(server_data, dict) else {}
    data = chapter.get("data", []) if isinstance(chapter, dict) else []
    if not isinstance(data, list):
        return []
    return [f for f in data if isinstance(f, str) and f]


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class MangadexScraper(BaseScraper):
    """MangaDex chapter and series scraper via the public REST API."""

    domain = DOMAIN
    name = "mangadex"

    def matches_url(self, url: str) -> bool:
        return is_chapter_url(url) or is_series_url(url)

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        return await self._scrape_series(url, client)

    async def _api_get_json(self, url: str, client: AsyncSession) -> dict:
        response = await BaseScraper._timeout_get(url, client)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise ScrapeError(
                "Unexpected response from the MangaDex API.",
                hint="The API may be down or rate-limiting this client.",
            ) from exc
        if not isinstance(data, dict) or data.get("result") != "ok":
            raise ScrapeError(
                "The MangaDex API reported an error for this URL.",
                hint="Check that the chapter/series still exists on mangadex.org.",
            )
        return data

    async def _manga_json(self, manga_id: str, client: AsyncSession) -> dict:
        url = (
            f"{_API}/manga/{manga_id}"
            "?includes[]=cover_art&includes[]=author&includes[]=artist"
        )
        data = await self._api_get_json(url, client)
        return data.get("data", {})

    async def _scrape_chapter(
        self, url: str, client: AsyncSession,
    ) -> ScrapedChapter:
        chapter_id = _extract_chapter_id(url)
        if not chapter_id:
            raise ScrapeError(
                "Unsupported mangadex.org URL.",
                hint="Expected a chapter URL like "
                     "https://mangadex.org/chapter/{chapter-uuid}",
            )

        chapter = await self._api_get_json(f"{_API}/chapter/{chapter_id}", client)
        chapter_data = chapter.get("data", {})
        attributes = chapter_data.get("attributes", {})

        manga_ids = _relationship_ids(chapter_data, "manga")
        if not manga_ids:
            raise ScrapeError(
                "This MangaDex chapter is not attached to a series.",
                hint="The chapter may be an external/one-off upload.",
            )
        manga_id = manga_ids[0]

        meta = _manga_fields(await self._manga_json(manga_id, client), manga_id)
        if not meta["series_title"]:
            raise ScrapeError(
                "Could not resolve the series for this MangaDex chapter.",
                hint="The series may have been deleted from MangaDex.",
            )

        server = await self._api_get_json(
            f"{_API}/at-home/server/{chapter_id}", client,
        )
        base_url = server.get("baseUrl")
        server_chapter = server.get("chapter", {})
        if not isinstance(server_chapter, dict):
            server_chapter = {}
        data_hash = server_chapter.get("hash")
        page_files = _page_files(server)
        if not base_url or not data_hash or not page_files:
            raise no_images_error(
                "MangaDex's at-home server returned no pages for this chapter."
            )

        images = [
            ImageItem(
                url=f"{str(base_url).rstrip('/')}/data/{data_hash}/{page_file}",
                page_number=i + 1,
            )
            for i, page_file in enumerate(page_files)
        ]

        chapter_number = attributes.get("chapter")
        chapter_number = str(chapter_number).strip() if chapter_number not in (None, "") else None
        chapter_title = attributes.get("title") or (
            f"Chapter {chapter_number}" if chapter_number else "Chapter"
        )

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=meta["series_title"],
                chapter_title=str(chapter_title),
                chapter_number=chapter_number,
                volume_number=str(attributes.get("volume")) if attributes.get("volume") else None,
                description=meta.get("description", ""),
                authors=meta.get("authors", []),
                artists=meta.get("artists", []),
                genres=meta.get("genres", []),
                status=meta.get("status"),
                language=attributes.get("translatedLanguage") or None,
                reading_direction="ltr",
                community_rating=meta.get("community_rating"),
                year=meta.get("year"),
                total_pages=len(images),
            ),
            source=SourceInfo(url=url, service=DOMAIN, post_id=chapter_id),
            images=images,
            cover_url=meta.get("cover_url", ""),
        )

    async def _scrape_series(
        self, url: str, client: AsyncSession,
    ) -> SeriesMetadata:
        manga_id = _extract_series_id(url)
        if not manga_id:
            raise ScrapeError(
                "Unsupported mangadex.org URL.",
                hint="Expected a series URL like "
                     "https://mangadex.org/title/{series-uuid}",
            )

        meta = _manga_fields(await self._manga_json(manga_id, client), manga_id)
        if not meta["series_title"]:
            raise ScrapeError(
                "Could not resolve the series on MangaDex.",
                hint="The series may have been deleted or made private.",
            )

        chapters: list[dict[str, Any]] = []
        offset = 0
        for _ in range(_FEED_PAGE_CAP):
            feed = await self._api_get_json(_feed_url(manga_id, offset), client)
            items = feed.get("data", [])
            for item in items:
                if not isinstance(item, dict):
                    continue
                attributes = item.get("attributes", {})
                if attributes.get("externalUrl"):
                    continue
                chapter_id = item.get("id")
                if not chapter_id:
                    continue
                number = attributes.get("chapter")
                number_s = str(number).strip() if number not in (None, "") else None
                title = attributes.get("title") or (
                    f"Chapter {number_s}" if number_s else "Chapter"
                )
                chapters.append({
                    "title": str(title),
                    "url": f"{BASE}/chapter/{chapter_id}",
                    "episode_no": number_s or str(title),
                    "volume": attributes.get("volume"),
                    "chapter": attributes.get("chapter"),
                })
            total = feed.get("total", 0)
            offset += _FEED_LIMIT
            if offset >= total:
                break
        else:
            raise ScrapeError(
                "MangaDex chapter list is unreasonably long; aborting.",
                hint=(
                    "This series exceeds the 10k-chapter safety cap — report "
                    "it if it is legitimate."
                ),
            )

        if not chapters:
            raise no_chapters_error()

        chapters.sort(
            key=lambda c: (
                _chapter_sort_key(c.get("volume")),
                _chapter_sort_key(c.get("chapter")),
            )
        )
        for entry in chapters:
            entry.pop("volume", None)
            entry.pop("chapter", None)

        return SeriesMetadata(
            series_title=meta["series_title"],
            description=meta.get("description", ""),
            cover_url=meta.get("cover_url", ""),
            title_no=manga_id,
            chapters=chapters,
        )
