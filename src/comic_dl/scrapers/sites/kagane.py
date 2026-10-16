"""Kagane scraper for the site's DRM-protected reader."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote, urlparse, urlsplit

from curl_cffi.requests import AsyncSession

from ... import webview
from ...antibot import looks_like_challenge
from ...cf import note_replay_dead, replay_dead
from ...errors import (
    SITE_BLOCKED,
    SITE_LAYOUT_CHANGED,
    SITE_NO_SERIES,
    SITE_NOT_RECOGNIZED,
    SITE_REQUEST_FAILED,
    ScrapeError,
)
from ...http import jar_cookies_for
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

DOMAIN = "kagane.org"

#: Hosts serving the same catalogue. kagane.org is now a mirror chooser
#: (its API answers only challenges), so its URLs are fetched from here.
CANONICAL_BASE = "https://kagane.si"
KAGANE_HOSTS = frozenset(
    {
        "kagane.org",
        "kagane.si",
        "kagane.cx",
        "kagane.moe",
        "kagane.net",
    }
)

_HOSTS_RE = r"(?:kagane\.org|kagane\.si|kagane\.cx|kagane\.moe|kagane\.net)"
_SERIES_PATH_RE = re.compile(rf"^https?://(?:www\.)?{_HOSTS_RE}/series/[^/]+/?$")
_CHAPTER_PATH_RE = re.compile(rf"^https?://(?:www\.)?{_HOSTS_RE}/series/[^/]+/reader/[^/]+/?$")

# Cover/avatar images (best-effort; the real CDN host comes from each book's
# ``cache_url`` and is never hardcoded).


def _base_for_url(url: str) -> str:
    """API base for ``url`` (kagane.org remapped: it no longer serves content)."""
    host = (urlsplit(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host in KAGANE_HOSTS and host != "kagane.org":
        return f"https://{host}"
    return CANONICAL_BASE


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a series page for this source."""
    return bool(_SERIES_PATH_RE.match(url))


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a chapter/gallery page for this source."""
    return bool(_CHAPTER_PATH_RE.match(url))


def _extract_ids(url: str) -> tuple[str, str | None]:
    """Return ``(series_id, book_id)`` from a kagane.org series/reader URL.

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
    """Book rows from a books-endpoint payload (``items``) or legacy inline."""
    if not isinstance(data, dict):
        return []
    for key in ("items", "series_books"):
        rows = data.get(key)
        if isinstance(rows, list):
            return [b for b in rows if isinstance(b, dict)]
    return []


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


def _cover_url(data: object, base: str) -> str:
    if not isinstance(data, dict):
        return ""
    covers = data.get("series_covers", [])
    if covers and isinstance(covers[0], dict) and covers[0].get("image_id"):
        return f"{base}/api/v2/image/{covers[0]['image_id']}"
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


def _chapters(books: list[dict], series_id: str, base: str) -> list[dict]:
    """Chapter list from walked book rows, ordered by ``sort_no``."""
    chapters: list[dict] = []
    for book in sorted(books, key=_sort_no):
        book_id = book.get("book_id")
        if not book_id:
            continue
        number = book.get("chapter_no")
        number_s = str(number).strip() if number not in (None, "") else None
        title = book.get("title") or (f"Chapter {number_s}" if number_s else "Chapter")
        chapters.append(
            {
                "title": str(title),
                "url": f"{base}/series/{series_id}/reader/{book_id}",
                "episode_no": number_s or str(title),
            }
        )
    return chapters


def _find_book(books: list[dict], book_id: str) -> dict:
    for book in books:
        if book.get("book_id") == book_id:
            return book
    return {}


def _series_fields(data: object, base: str) -> dict:
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
        "cover_url": _cover_url(data, base),
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


def _raise_for_status(response: Any) -> None:
    """Raise for a bad API response, mapping 404 to a removal error."""
    if getattr(response, "status_code", 200) == 404:
        raise ScrapeError(
            "Page not found on kagane.org.",
            hint="The series may have been removed, or this chapter link is dead.",
            site_error_code=SITE_NO_SERIES,
        )
    response.raise_for_status()


def _has_clearance(url: str) -> bool:
    """True when the jar holds a ``cf_clearance`` for ``url``'s host."""
    return "cf_clearance" in jar_cookies_for(url)


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
    site_id = "kagane"
    display_name = "Kagane"
    content_warning = "safe"
    chapter_url_pattern = "/series/{id}/reader/{book}"
    series_url_pattern = "/series/{id}/"
    version = "1.1.0"
    test_url = "https://kagane.si/series/019c2b1c-cc9b-745f-afed-09c230759162/reader/019c2bda-f6fe-7a0c-a4db-669f54b67a86"
    test_url_kind = "chapter"
    minimum_core_version = "0.0.2"

    series_url_re = _SERIES_PATH_RE
    chapter_url_re = _CHAPTER_PATH_RE

    def matches_url(self, url: str) -> bool:
        return bool(self.chapter_url_re.match(url)) or bool(self.series_url_re.match(url))

    def matches_series_url(self, url: str) -> bool:
        return bool(self.series_url_re.match(url))

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
        base: str,
        headers: dict[str, str] | None = None,
        body: str | None = None,
    ) -> Any:
        """Fetch a kagane API endpoint, preferring the stored cookie.

        A jarred ``cf_clearance`` is replayed plain-HTTP first, so a fresh
        cookie never costs a webview window — the session is only opened
        when the replay is actually challenged. One failed probe per host
        per run is enough: after that the session is used directly for the
        rest of the run. Without a stored cookie the session is used
        directly: a plain attempt would only burn a request to learn the
        same thing. Kagane binds clearance to the minting TLS fingerprint,
        so when the replay is challenged the request falls back to the
        :class:`~comic_dl.webview.WebViewSession` (same-origin XHR carrying
        cookies *and* fingerprint); otherwise — headless CI, webview
        disabled — plain HTTP with the stored cookie is best-effort.
        """
        json_body = json.loads(body) if body else None
        host = (urlsplit(url).hostname or "").lower()
        if not replay_dead(host) and _has_clearance(url):
            resp = await BaseScraper._timeout_get(
                url,
                client,
                method=method,
                headers=headers,
                json=json_body,
                challenge_retry=False,
            )
            if not looks_like_challenge(
                resp.status_code,
                getattr(resp, "headers", None),
                getattr(resp, "text", "") or "",
            ):
                return resp
            note_replay_dead(host)
        if webview.session_enabled():
            try:
                session = await webview.ensure_session(base)
            except Exception:
                session = None
            if session is not None:
                await await_ratelimit(urlsplit(url).hostname or DOMAIN)
                status, resp_headers, content = await session.request(
                    method,
                    url,
                    headers=headers,
                    body=body,
                )
                return _SessionResponse(status, resp_headers, content)

        resp = await BaseScraper._timeout_get(
            url,
            client,
            method=method,
            headers=headers,
            json=json_body,
        )
        # A challenge that the solver could not clear is returned to us as-is
        # (see ``cf.retry_challenge_once``). Without this check the caller's
        # ``raise_for_status()`` turns the 403 into a bare HTTPError, which the
        # CLI reports as "Network error. Check your internet connection." --
        # sending the user to debug their router instead of passing the
        # challenge in a browser window.
        if looks_like_challenge(
            resp.status_code,
            getattr(resp, "headers", None),
            getattr(resp, "text", "") or "",
        ):
            raise ScrapeError(
                "Cloudflare challenged the kagane.org request.",
                hint=(
                    "This site only answers the webview solver — run with a "
                    "display and --solver auto (or webview) to pass the "
                    "challenge in the browser window."
                ),
                site_error_code=SITE_BLOCKED,
            )
        return resp

    async def _series_json(self, series_id: str, base: str, client: AsyncSession) -> dict:
        """Series metadata + books from the Kagane API.

        Served through the long-lived webview session when possible (see
        :meth:`_api_fetch`); the API sits behind Cloudflare, so plain HTTP
        replay of a harvested cookie is unreliable.
        """
        url = f"{base}/api/v2/series/{series_id}"
        response = await self._api_fetch("GET", url, client, base=base)
        _raise_for_status(response)
        data = response.json()
        if not isinstance(data, dict):
            raise ScrapeError(
                "Unexpected series response from kagane.org.",
                hint="The API shape may have changed; try updating comic-dl.",
                site_error_code=SITE_LAYOUT_CHANGED,
            )
        return data

    async def _series_books(self, series_id: str, base: str, client: AsyncSession) -> list[dict]:
        """Every book row, following the books endpoint's ``next_cursor`` pages."""
        books: list[dict] = []
        seen_cursors: set[str] = set()
        before: str | None = None
        while True:
            if before is None:
                query = "?limit=100"
            else:
                query = f"?limit=100&before={quote(before, safe='')}"
            response = await self._api_fetch(
                "GET", f"{base}/api/v2/series/{series_id}/books{query}", client, base=base
            )
            _raise_for_status(response)
            data = response.json()
            if not isinstance(data, dict):
                raise ScrapeError(
                    "Unexpected books response from kagane.org.",
                    hint="The API shape may have changed; try updating comic-dl.",
                    site_error_code=SITE_LAYOUT_CHANGED,
                )
            books.extend(_books(data))
            cursor = data.get("next_cursor")
            if not cursor or not isinstance(cursor, str) or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
            before = cursor
        return books

    async def _chapter_tokens(self, book_id: str, base: str, client: AsyncSession) -> dict:
        """Signed DRM payload for one book via Kagane's public API.

        Mirrors haruneko's kagane connector: an integrity token is minted at
        ``POST /api/integrity``, then the book manifest is requested with that
        token in the ``X-Integrity-Token`` header. Both calls go through the
        webview session when available (see :meth:`_api_fetch`).
        """
        integrity = await self._api_fetch(
            "POST",
            f"{base}/api/integrity",
            client,
            base=base,
        )
        _raise_for_status(integrity)
        token_data = integrity.json()
        if not isinstance(token_data, dict) or not token_data.get("token"):
            raise ScrapeError(
                "Unexpected integrity response from kagane.org.",
                hint="The DRM handshake may have changed; try updating comic-dl.",
                site_error_code=SITE_LAYOUT_CHANGED,
            )

        books = await self._api_fetch(
            "POST",
            f"{base}/api/v2/books/{book_id}?is_datasaver=false",
            client,
            base=base,
            headers={"X-Integrity-Token": str(token_data["token"])},
            body="{}",
        )
        _raise_for_status(books)
        data = books.json()
        if not isinstance(data, dict):
            raise ScrapeError(
                "Unexpected book response from kagane.org.",
                hint="The API shape may have changed; try updating comic-dl.",
                site_error_code=SITE_LAYOUT_CHANGED,
            )
        return data

    async def _scrape_chapter(
        self,
        url: str,
        client: AsyncSession,
    ) -> ScrapedChapter:
        series_id, book_id = _extract_ids(url)
        if not book_id:
            raise ScrapeError(
                "Unsupported kagane.org URL.",
                hint="Expected a reader URL like https://kagane.org/series/{series}/reader/{book}",
                site_error_code=SITE_NOT_RECOGNIZED,
            )
        base = _base_for_url(url)
        reader_url = f"{base}/series/{series_id}/reader/{book_id}"

        try:
            data = await self._chapter_tokens(book_id, base, client)
        except Exception as exc:
            if isinstance(exc, (ValueError, ScrapeError)):
                raise
            if _is_cf_challenge_error(exc):
                raise ScrapeError(
                    "Cloudflare challenged the kagane.org request.",
                    hint=(
                        "Run again to let the webview solver pass the "
                        "challenge, or set a stored `cf_clearance` via "
                        "`comic-dl cookie set`. URL: " + url
                    ),
                    site_error_code=SITE_BLOCKED,
                ) from exc
            raise ScrapeError(
                "Could not reach the kagane.org API.",
                hint=(
                    "This site requires a passing Cloudflare session — "
                    "install the webview solver (`comic-dl[webview]`) and "
                    "run once, or set a stored `cf_clearance` via "
                    "`comic-dl cookie set`. URL: " + url
                ),
                site_error_code=SITE_REQUEST_FAILED,
            ) from exc

        image_urls = build_image_urls(data, book_id)
        if not image_urls:
            raise no_images_error()

        images = [
            ImageItem(url=image_url, page_number=i + 1) for i, image_url in enumerate(image_urls)
        ]

        try:
            series_data = await self._series_json(series_id, base, client)
        except Exception:
            series_data = {}
        try:
            books = await self._series_books(series_id, base, client)
        except Exception:
            books = []

        meta = _series_fields(series_data, base)
        book = _find_book(books, book_id)
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
        self,
        url: str,
        client: AsyncSession,
    ) -> SeriesMetadata:
        series_id, _ = _extract_ids(url)
        if not series_id:
            raise ScrapeError(
                "Unsupported kagane.org URL.",
                hint="Expected a series URL like https://kagane.org/series/{series}",
                site_error_code=SITE_NOT_RECOGNIZED,
            )
        base = _base_for_url(url)
        data = await self._series_json(series_id, base, client)
        chapters = _chapters(await self._series_books(series_id, base, client), series_id, base)
        if not chapters:
            raise no_chapters_error()
        meta = _series_fields(data, base)
        return SeriesMetadata(
            series_title=meta.get("series_title") or "Untitled",
            description=meta.get("description", ""),
            cover_url=meta.get("cover_url", ""),
            title_no=series_id,
            chapters=chapters,
        )
