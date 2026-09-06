"""Kagane scraper for the site's DRM-protected reader."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse, urlsplit

from curl_cffi.requests import AsyncSession

from ... import webview
from ...antibot import looks_like_challenge
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
from ...rate import await_ratelimit
from ..base import BaseScraper, no_chapters_error, no_images_error
from ..registry import register_scraper

DOMAIN = "kagane.to"
BASE = "https://kagane.to"
_API = f"{BASE}/api/v2"

_SERIES_PATH_RE = re.compile(
    r"^https?://(?:www\.)?kagane\.to/series/[^/]+/?$"
)
_CHAPTER_PATH_RE = re.compile(
    r"^https?://(?:www\.)?kagane\.to/series/[^/]+/reader/[^/]+/?$"
)

# Cover/avatar images (best-effort; the real CDN host comes from each book's
# ``cache_url`` and is never hardcoded).
_IMAGE_BASE_URL = "https://kagane.to/api/v2/image"


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a series page for this source."""
    return bool(_SERIES_PATH_RE.match(url))


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a chapter/gallery page for this source."""
    return bool(_CHAPTER_PATH_RE.match(url))


def _extract_ids(url: str) -> tuple[str, str | None]:
    """Return ``(series_id, book_id)`` from a kagane.to series/reader URL.

    ``book_id`` is ``None`` for a plain series page. Kagane IDs are opaque
    (UUIDs or short base32 tokens), so the raw path segments are used.
    """
    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "series":
        series_id = parts[1]
        if len(parts) >= 4 and parts[2] == "reader":
            return series_id, parts[3]
        return series_id, None
    return "", None


def build_image_urls(data: object, book_id: str) -> list[str]:
    """Page image URLs from the books-API payload.

    ``data`` is the ``/api/v2/books/{id}`` response: ``cache_url`` (the CDN
    host), ``access_token`` (signed, per-book) and ``manifest.pages`` (ordered
    ``page_id``/``ext``). Pages keep their manifest order so download order is
    stable. Returns ``[]`` for malformed input.
    """
    if not isinstance(data, dict):
        return []
    cache_url = data.get("cache_url") or data.get("cacheUrl")
    access_token = data.get("access_token")
    manifest = data.get("manifest")
    pages = manifest.get("pages") if isinstance(manifest, dict) else None
    if not cache_url or not access_token or not isinstance(pages, list) or not pages:
        return []
    cache_url = str(cache_url).rstrip("/")
    urls: list[str] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_id = page.get("page_id") or page.get("pageId")
        if not page_id:
            continue
        ext = page.get("ext") or "webp"
        urls.append(
            f"{cache_url}/api/v2/books/page/{book_id}/{page_id}.{ext}"
            f"?is_datasaver=false&token={access_token}"
        )
    return urls


def _books(data: object) -> list[dict]:
    if not isinstance(data, dict):
        return []
    return [b for b in data.get("series_books", []) if isinstance(b, dict)]


def _genres(data: object) -> list[str]:
    if not isinstance(data, dict):
        return []
    out: list[str] = []
    for genre in data.get("genres", []):
        if isinstance(genre, dict) and genre.get("genre_name"):
            out.append(str(genre["genre_name"]))
    return out


def _staff_names(data: object) -> list[str]:
    if not isinstance(data, dict):
        return []
    out: list[str] = []
    for member in data.get("series_staff", []):
        if isinstance(member, dict) and member.get("name"):
            out.append(str(member["name"]))
    return out


def _cover_url(data: object) -> str:
    if not isinstance(data, dict):
        return ""
    covers = data.get("series_covers", [])
    if covers and isinstance(covers[0], dict) and covers[0].get("image_id"):
        return f"{_IMAGE_BASE_URL}/{covers[0]['image_id']}"
    return ""


def _sort_no(book: dict) -> float:
    raw = book.get("sort_no")
    if isinstance(raw, bool):
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.strip())
        except ValueError:
            return 0.0
    return 0.0


def _chapters(data: object, series_id: str) -> list[dict]:
    """Chapter list from the API payload, ordered by ``sort_no``."""
    chapters: list[dict] = []
    for book in sorted(_books(data), key=_sort_no):
        book_id = book.get("book_id")
        if not book_id:
            continue
        number = book.get("chapter_no")
        number_s = str(number).strip() if number not in (None, "") else None
        title = book.get("title") or (
            f"Chapter {number_s}" if number_s else "Chapter"
        )
        chapters.append({
            "title": str(title),
            "url": f"{BASE}/series/{series_id}/reader/{book_id}",
            "episode_no": number_s or str(title),
        })
    return chapters


def _find_book(data: object, book_id: str) -> dict:
    for book in _books(data):
        if book.get("book_id") == book_id:
            return book
    return {}


def _series_fields(data: object) -> dict:
    """Flat best-effort metadata shared by the chapter and series models."""
    if not isinstance(data, dict):
        return {}
    rating = data.get("average_rating")
    community_rating = float(rating) if isinstance(rating, (int, float)) else None
    year = data.get("start_year")
    year = int(year) if isinstance(year, (int, float)) else None
    status = data.get("publication_status") or None
    language = data.get("translated_language") or None
    return {
        "series_title": str(data.get("title", "")),
        "description": str(data.get("description", "")),
        "genres": _genres(data),
        "artists": _staff_names(data),
        "status": str(status) if status else None,
        "language": str(language) if language else None,
        "year": year,
        "community_rating": community_rating,
        "cover_url": _cover_url(data),
    }


class _SessionResponse:
    """Response shim so the plain-HTTP and webview-session paths share one
    caller contract (``status_code``, ``headers``, ``json()``,
    ``raise_for_status()``)."""

    def __init__(self, status: int, headers: dict, body: bytes) -> None:
        self.status_code = status
        self.headers = headers
        self.content = body
        self.text = body.decode("utf-8", errors="replace")
        self.ok = 200 <= status < 400

    def raise_for_status(self) -> None:
        if not self.ok:
            from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError

            raise CurlHTTPError(f"HTTP Error {self.status_code}", response=self)

    def json(self):
        return json.loads(self.text)


def _is_cf_challenge_error(exc: BaseException) -> bool:
    """True when ``exc`` carries a Cloudflare challenge response.

    Kagane's webview-session shim raises :class:`CurlHTTPError` with the
    response attached; plain-HTTP failures surface the same way. Checking
    here keeps the user-facing message honest ("Cloudflare challenged…")
    instead of the generic "HTTPError" wrapper that hides the actual cause.
    """
    from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError

    if not isinstance(exc, CurlHTTPError):
        return False
    resp = getattr(exc, "response", None)
    if resp is None:
        return False
    return looks_like_challenge(
        getattr(resp, "status_code", 0) or 0,
        getattr(resp, "headers", None),
        getattr(resp, "text", "") or "",
    )


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class KaganeScraper(BaseScraper):
    """Kagane chapter and series scraper with webview challenge solving."""

    domain = DOMAIN
    name = "kagane"

    def matches_url(self, url: str) -> bool:
        return is_chapter_url(url) or is_series_url(url)

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        return await self._scrape_series(url, client)

    async def _api_fetch(
        self,
        method: str,
        url: str,
        client: AsyncSession,
        *,
        headers: dict[str, str] | None = None,
        body: str | None = None,
    ) -> Any:
        """Fetch a kagane API endpoint, preferring the webview session.

        Kagane binds its ``cf_clearance`` to the WebKit TLS fingerprint that
        minted it, so a harvested cookie cannot be replayed by curl_cffi. When
        a :class:`~comic_dl.webview.WebViewSession` is available, the request
        runs as a same-origin XHR inside the page (carrying cookies *and*
        fingerprint). Otherwise — headless CI, webview disabled — fall back to
        plain HTTP with the stored ``cf_clearance``, which is best-effort.
        """
        if webview.session_enabled():
            try:
                session = await webview.ensure_session(BASE)
            except Exception:
                session = None
            if session is not None:
                await await_ratelimit(urlsplit(url).hostname or DOMAIN)
                status, resp_headers, content = await session.request(
                    method, url, headers=headers, body=body,
                )
                return _SessionResponse(status, resp_headers, content)

        json_body = json.loads(body) if body else None
        return await BaseScraper._timeout_get(
            url, client, method=method, headers=headers, json=json_body,
        )

    async def _series_json(self, series_id: str, client: AsyncSession) -> dict:
        """Series metadata + books from the Kagane API.

        Served through the long-lived webview session when possible (see
        :meth:`_api_fetch`); the API sits behind Cloudflare, so plain HTTP
        replay of a harvested cookie is unreliable.
        """
        url = f"{_API}/series/{series_id}"
        response = await self._api_fetch("GET", url, client)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ScrapeError(
                "Unexpected series response from kagane.to.",
                hint="The API shape may have changed; try updating comic-dl.",
            )
        return data

    async def _chapter_tokens(
        self, book_id: str, client: AsyncSession
    ) -> dict:
        """Signed DRM payload for one book via Kagane's public API.

        Mirrors haruneko's kagane connector: an integrity token is minted at
        ``POST /api/integrity``, then the book manifest is requested with that
        token in the ``X-Integrity-Token`` header. Both calls go through the
        webview session when available (see :meth:`_api_fetch`).
        """
        integrity = await self._api_fetch(
            "POST", f"{BASE}/api/integrity", client,
        )
        integrity.raise_for_status()
        token_data = integrity.json()
        if not isinstance(token_data, dict) or not token_data.get("token"):
            raise ScrapeError(
                "Unexpected integrity response from kagane.to.",
                hint="The DRM handshake may have changed; try updating comic-dl.",
            )

        books = await self._api_fetch(
            "POST",
            f"{_API}/books/{book_id}?is_datasaver=false",
            client,
            headers={"X-Integrity-Token": str(token_data["token"])},
            body="{}",
        )
        books.raise_for_status()
        data = books.json()
        if not isinstance(data, dict):
            raise ScrapeError(
                "Unexpected book response from kagane.to.",
                hint="The API shape may have changed; try updating comic-dl.",
            )
        return data

    async def _scrape_chapter(
        self, url: str, client: AsyncSession,
    ) -> ScrapedChapter:
        series_id, book_id = _extract_ids(url)
        if not book_id:
            raise ScrapeError(
                "Unsupported kagane.to URL.",
                hint="Expected a reader URL like "
                     "https://kagane.to/series/{series}/reader/{book}",
            )
        reader_url = f"{BASE}/series/{series_id}/reader/{book_id}"

        try:
            data = await self._chapter_tokens(book_id, client)
        except Exception as exc:
            if isinstance(exc, (ValueError, ScrapeError)):
                raise
            if _is_cf_challenge_error(exc):
                raise ScrapeError(
                    "Cloudflare challenged the kagane.to request.",
                    hint=(
                        "Run again to let the webview solver pass the "
                        "challenge, or set a stored `cf_clearance` via "
                        "`comic-dl cookie set`. URL: " + url
                    ),
                ) from exc
            raise ScrapeError(
                f"Could not reach the kagane.to API ({type(exc).__name__}).",
                hint=(
                    "This site requires a passing Cloudflare session — "
                    "install the webview solver (`comic-dl[webview]`) and "
                    "run once, or set a stored `cf_clearance` via "
                    "`comic-dl cookie set`. URL: " + url
                ),
            ) from exc

        image_urls = build_image_urls(data, book_id)
        if not image_urls:
            raise no_images_error()

        images = [
            ImageItem(url=image_url, page_number=i + 1)
            for i, image_url in enumerate(image_urls)
        ]

        try:
            series_data = await self._series_json(series_id, client)
        except Exception:
            series_data = {}

        meta = _series_fields(series_data)
        book = _find_book(series_data, book_id)
        number = book.get("chapter_no")
        chapter_number = str(number).strip() if number not in (None, "") else None
        chapter_title = book.get("title") or (
            f"Chapter {chapter_number}" if chapter_number else "Chapter"
        )

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=meta.get("series_title") or "Untitled",
                chapter_title=str(chapter_title),
                chapter_number=chapter_number,
                description=meta.get("description", ""),
                artists=meta.get("artists", []),
                genres=meta.get("genres", []),
                status=meta.get("status"),
                language=meta.get("language"),
                reading_direction="ltr",
                community_rating=meta.get("community_rating"),
                year=meta.get("year"),
                total_pages=len(images),
            ),
            source=SourceInfo(url=reader_url, service=DOMAIN, post_id=book_id),
            images=images,
            cover_url=meta.get("cover_url", ""),
        )

    async def _scrape_series(
        self, url: str, client: AsyncSession,
    ) -> SeriesMetadata:
        series_id, _ = _extract_ids(url)
        if not series_id:
            raise ScrapeError(
                "Unsupported kagane.to URL.",
                hint="Expected a series URL like https://kagane.to/series/{series}",
            )
        data = await self._series_json(series_id, client)
        chapters = _chapters(data, series_id)
        if not chapters:
            raise no_chapters_error()
        meta = _series_fields(data)
        return SeriesMetadata(
            series_title=meta.get("series_title") or "Untitled",
            description=meta.get("description", ""),
            cover_url=meta.get("cover_url", ""),
            title_no=series_id,
            chapters=chapters,
        )
