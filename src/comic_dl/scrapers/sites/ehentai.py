"""e-hentai.org gallery and gallery-API scraper."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import (
    ConnectionError as CurlConnectionError,
)
from curl_cffi.requests.exceptions import (
    HTTPError as CurlHTTPError,
)
from curl_cffi.requests.exceptions import (
    Timeout as CurlTimeout,
)

from ...errors import ScrapeError, ScrapeTimeout
from ...models import (
    ChapterInfo,
    ImageItem,
    PostMetadata,
    ScrapedChapter,
    SourceInfo,
    chapter_to_post_metadata,
)
from ...utils import (
    PART_PATTERN,
    clean_title,
    image_source_name,
    sanitize_filename,
)
from ..base import BaseScraper, _attr_text, no_images_error
from ..refresh import register_image_refresher
from ..registry import register_scraper

_BRACKET_GROUP_RE = re.compile(r'^\[([^\]]+)\]\s*')
_BRACKET_GROUP_ARTIST_RE = re.compile(r'^(.+?)\s*\(([^)]+)\)\s*$')


def _decode_text(resp: Any) -> str:
    """Decode a response body as UTF-8, falling back to ``.text``.

    curl_cffi guesses the charset from the ``Content-Type`` header, so a
    mislabeled ``iso-8859-1`` header over UTF-8 bytes produces mojibake via
    ``.text`` and ``U+FFFD`` via its utf-8-sig fallback. Decode the raw
    bytes explicitly instead, and only fall back when the body truly is not
    UTF-8.
    """
    content = getattr(resp, "content", None)
    if isinstance(content, bytes):
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return getattr(resp, "text", "") or ""


def _decode_json(resp: Any) -> dict:
    """Parse a JSON response body as UTF-8 (see :func:`_decode_text`)."""
    content = getattr(resp, "content", None)
    if isinstance(content, bytes):
        try:
            return json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    return resp.json()


def _extract_bracket_prefix(title: str) -> tuple[str | None, str | None]:
    m = _BRACKET_GROUP_RE.match(title)
    if not m:
        return (None, None)
    content = m.group(1).strip()
    am = _BRACKET_GROUP_ARTIST_RE.match(content)
    if am:
        return (am.group(1).strip(), am.group(2).strip())
    return (content, None)


# e-hentai ``language:`` tags → ISO 639-1 short codes. Only real language
# names map; tags like ``translated`` or ``textless narrative`` are not
# languages and must not leak into ``LanguageISO``.
_LANGUAGE_ISO_MAP: dict[str, str] = {
    "english": "en",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "portuguese": "pt",
    "russian": "ru",
    "dutch": "nl",
    "arabic": "ar",
    "hindi": "hi",
    "thai": "th",
    "vietnamese": "vi",
    "indonesian": "id",
    "malay": "ms",
    "tamil": "ta",
    "telugu": "te",
    "filipino": "tl",
    "tagalog": "tl",
    "polish": "pl",
    "swedish": "sv",
    "danish": "da",
    "norwegian": "no",
    "finnish": "fi",
    "turkish": "tr",
    "greek": "el",
    "hebrew": "he",
    "hungarian": "hu",
    "czech": "cs",
    "romanian": "ro",
    "ukrainian": "uk",
    "bulgarian": "bg",
    "croatian": "hr",
    "serbian": "sr",
    "slovenian": "sl",
    "slovak": "sk",
    "latin": "la",
}


def _extract_tag_metadata(
    tags: list[str],
) -> tuple[list[str], list[str], str | None]:
    artists: list[str] = []
    genres: list[str] = []
    language: str | None = None
    exclude = frozenset({"artist", "language", "parody", "character", "group", "female", "male"})

    for tag in tags:
        if ":" in tag:
            ns, value = tag.split(":", 1)
            ns = ns.strip().lower()
            value = value.strip()
            if ns == "artist" and value:
                artists.append(value)
            elif ns == "language" and value and language is None:
                language = _LANGUAGE_ISO_MAP.get(value.strip().lower())
            elif ns not in exclude and value:
                genres.append(value)
        else:
            tag_stripped = tag.strip()
            if tag_stripped:
                genres.append(tag_stripped)

    return (
        list(dict.fromkeys(artists)),
        list(dict.fromkeys(genres)),
        language,
    )


async def _api_gdata(gid: int, token: str, client: AsyncSession) -> dict:
    # Route the metadata API through the same validated path as the rest of
    # the site: hard timeout, cookie absorption, rate limiting, and redirect
    # refusal are all handled by _timeout_get.
    resp = await BaseScraper._timeout_get(
        "https://api.e-hentai.org/api.php",
        client,
        method="POST",
        rate=2.0,
        json={
            "method": "gdata",
            "gidlist": [[gid, token]],
            "namespace": 1,
        },
        use_cache=False,
    )
    resp.raise_for_status()
    data = _decode_json(resp)
    if "error" in data:
        raise ScrapeError(f"e-hentai API error: {data['error']}")
    return data["gmetadata"][0]


def _extract_series_chapter(title: str) -> tuple[str, str]:
    part = PART_PATTERN.search(title)
    if part:
        chapter = f"Chapter {part.group(1)}"
        series = title[: part.start()].strip().rstrip(",").strip()
    else:
        chapter = title
        series = title

    series = clean_title(series)
    chapter = clean_title(chapter)

    return (
        sanitize_filename(series) or "Untitled",
        sanitize_filename(chapter) or "Chapter 1",
    )


async def _fetch_gallery_page(page_url: str, client: AsyncSession) -> list[str]:
    resp = await BaseScraper._timeout_get(page_url, client, use_cache=True)
    resp.raise_for_status()
    soup = BeautifulSoup(_decode_text(resp), "lxml")
    urls: list[str] = []
    for a in soup.select("#gdt a"):
        href = _attr_text(a.get("href"))
        if "/s/" in href:
            # Gallery thumbnails link to relative /s/<token>/<gid>-<n> paths;
            # resolve them so downstream fetches target an absolute, validable
            # URL instead of a host-less path.
            urls.append(urljoin(page_url, href))
    return urls


_GALLERY_PAGE_SEM = asyncio.Semaphore(6)
_IMAGE_PAGE_SEM = asyncio.Semaphore(6)
# /s/ image-page fetches are cheap HTML (a few KB each) but e-hentai still
# throttles sustained pageview bursts on large galleries; resolve them at the
# same rate as the site default (2.0/s) instead of hammering faster.
_IMAGE_PAGE_RATE = 2.0

# A gallery page that stalls or drops its connection must not abort the whole
# gallery enumeration (it costs up to ~20 images); retry transient failures
# before giving up. 509 is e-hentai's H@H bandwidth-limit throttle code.
_GALLERY_PAGE_RETRIES = 3
_GALLERY_PAGE_RETRYABLE_HTTP = frozenset({429, 500, 502, 503, 504, 509})


def _is_transient_page_error(exc: BaseException) -> bool:
    """Whether ``exc`` is worth re-fetching a gallery page over."""
    if isinstance(
        exc, (ScrapeTimeout, CurlConnectionError, CurlTimeout, ConnectionError, TimeoutError)
    ):
        return True
    if isinstance(exc, CurlHTTPError):
        resp = getattr(exc, "response", None)
        return resp is not None and resp.status_code in _GALLERY_PAGE_RETRYABLE_HTTP
    return False


async def _fetch_gallery_page_with_retry(page_url: str, client: AsyncSession) -> list[str]:
    """Fetch one gallery page, retrying transient failures with backoff."""
    last_exc: BaseException | None = None
    for attempt in range(_GALLERY_PAGE_RETRIES):
        try:
            return await _fetch_gallery_page(page_url, client)
        except Exception as exc:
            if not _is_transient_page_error(exc):
                raise
            last_exc = exc
            if attempt < _GALLERY_PAGE_RETRIES - 1:
                await asyncio.sleep((attempt + 1) * 0.8)
    if last_exc is None:  # pragma: no cover - the loop always sets it
        raise RuntimeError("gallery page retries exhausted without an error")
    raise last_exc


async def _gallery_page_urls(base_url: str, filecount: int, client: AsyncSession) -> list[str]:
    num_pages = (filecount + 19) // 20
    page_urls = [
        base_url if p == 0 else f"{base_url}?p={p}" for p in range(num_pages)
    ]

    async def _limited_fetch(pu: str) -> list[str]:
        async with _GALLERY_PAGE_SEM:
            return await _fetch_gallery_page_with_retry(pu, client)

    results = await asyncio.gather(
        *[_limited_fetch(pu) for pu in page_urls], return_exceptions=True
    )
    urls: list[str] = []
    for r in results:
        if isinstance(r, BaseException):
            raise r
        urls.extend(r)
    return urls


async def _image_page_url(
    page_url: str, client: AsyncSession, sem: asyncio.Semaphore,
    use_cache: bool = True,
) -> tuple[str, str] | None:
    async with sem:
        for attempt in range(3):
            try:
                resp = await BaseScraper._timeout_get(
                    page_url, client, rate=_IMAGE_PAGE_RATE, use_cache=use_cache
                )
                resp.raise_for_status()
                soup = BeautifulSoup(_decode_text(resp), "lxml")
                img = soup.select_one("img#img")
                if img and img.get("src"):
                    src = _attr_text(img.get("src")).replace("&amp;", "&")
                    _, ext = src.rsplit(".", 1)
                    ext = ext.split("?")[0].lower()
                    if ext not in ("jpg", "jpeg", "png", "webp", "gif", "bmp"):
                        ext = "webp"
                    return (src, ext)
                # A 200 HTML response that lacks the image element is e-hentai's
                # throttle page, not a broken page — treat it as transient and
                # retry with backoff instead of silently dropping the page.
                if attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))
                continue
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(attempt + 1)
                continue
        return None


@register_image_refresher("e-hentai.org", "exhentai.org")
async def _refresh_stale_image(
    client: AsyncSession, item: ImageItem
) -> ImageItem | None:
    """Re-mint an expired H@H keystamp link from its ``/s/`` page.

    The downloader calls this when a retryable failure suggests the link
    went stale (timeout on a dead node, or a 200 HTML stub instead of
    image bytes). Re-fetching ``item.source_url`` makes e-hentai assign a
    fresh node + keystamp; returning ``None`` (or the same URL) tells it
    to retry the original link unchanged.
    """
    if not item.source_url:
        return None
    result = await _image_page_url(item.source_url, client, _IMAGE_PAGE_SEM, use_cache=False)
    if result is None:
        return None
    img_url, _ext = result
    # Preserve filename/page ordering; only the link is refreshed.
    return replace(item, url=img_url)


async def _iter_image_items(
    base_url: str,
    filecount: int,
    client: AsyncSession,
    sem: asyncio.Semaphore | None = None,
) -> AsyncIterator[ImageItem]:
    """Resolve gallery images lazily, yielding ``ImageItem``s in page order.

    Every ``/s/`` page is fetched concurrently (bounded by ``sem``), but
    results are yielded strictly in page order so filenames stay stable and a
    consumer can start downloading as soon as the first pages resolve instead
    of waiting for the whole gallery to be scraped.
    """
    sem = sem or _IMAGE_PAGE_SEM
    page_urls = await _gallery_page_urls(base_url, filecount, client)
    tasks = [asyncio.create_task(_image_page_url(pu, client, sem)) for pu in page_urls]
    try:
        for idx, (pu, task) in enumerate(zip(page_urls, tasks, strict=True), start=1):
            result = await task
            if result is not None:
                img_url, _ext = result
                yield ImageItem(url=img_url, page_number=idx, source_url=pu)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


@register_scraper(domain="e-hentai.org")
class EHentaiScraper(BaseScraper):
    """e-hentai.org gallery scraper with lazy, streaming image resolution."""

    domain = "e-hentai.org"
    name = "e-hentai"
    # The CLI can resolve image URLs lazily (see :meth:`iter_images`) so
    # downloads overlap URL resolution instead of waiting for a full scrape.
    streaming_images = True

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def scrape_meta(self, url: str, client: AsyncSession) -> PostMetadata:
        """Metadata only — no image URL resolution (used by the streaming path).

        ``total_pages`` still reports the gallery size so callers can show
        progress and size estimates before any image URL is known.
        """
        skel = await self._gallery_skeleton(url, client)
        return chapter_to_post_metadata(
            ScrapedChapter(
                info=skel["info"],
                source=skel["source"],
                images=[],
                cover_url=skel["cover_url"],
            )
        )

    async def iter_images(
        self,
        url: str,
        client: AsyncSession,
        total_pages: int | None = None,
    ) -> AsyncIterator[ImageItem]:
        """Yield gallery images in page order as their URLs resolve.

        ``total_pages`` avoids a redundant metadata round-trip when the caller
        already scraped it (e.g. from :meth:`scrape_meta`).
        """
        if total_pages is None:
            m = re.match(
                r"^https?://(?:www\.)?e-hentai\.org/g/(\d+)/([a-f0-9]+)/?",
                url,
            )
            if not m:
                raise ScrapeError(
                    f"Invalid e-hentai gallery URL: {url}",
                    hint="Expected a gallery URL like "
                         "https://e-hentai.org/g/{id}/{token}/",
                )
            gid, token = int(m.group(1)), m.group(2)
            meta = await _api_gdata(gid, token, client)
            total_pages = int(meta.get("filecount", 0))
        if not total_pages:
            return
        base_url = url.rstrip("/") + "/"
        async for item in _iter_image_items(base_url, total_pages, client):
            yield ImageItem(
                url=item.url,
                page_number=item.page_number,
                filename=image_source_name(item.page_number, item.url),
                source_url=item.source_url,
            )

    async def _gallery_skeleton(
        self, url: str, client: AsyncSession
    ) -> dict[str, Any]:
        """Everything about a gallery except its image URLs (one API call)."""
        m = re.match(
            r"^https?://(?:www\.)?e-hentai\.org/g/(\d+)/([a-f0-9]+)/?",
            url,
        )
        if not m:
            raise ScrapeError(
                f"Invalid e-hentai gallery URL: {url}",
                hint="Expected a gallery URL like "
                     "https://e-hentai.org/g/{id}/{token}/",
            )
        gid, token = int(m.group(1)), m.group(2)

        meta = await _api_gdata(gid, token, client)
        tags = meta.get("tags", [])
        full_title = meta.get("title", "")
        filecount = int(meta.get("filecount", 0))

        if filecount == 0:
            raise ScrapeError(
                "Gallery has no images.",
                hint="The gallery may have been removed or is private.",
            )

        if not full_title:
            # The API rarely omits the title; fall back to the first tag so
            # downstream naming never degrades to a bare placeholder.
            full_title = next(iter(tags), "") or "Untitled Gallery"

        series_title, chapter_title = _extract_series_chapter(full_title)

        artists, genres, language = _extract_tag_metadata(tags)
        _, bracket_artist = _extract_bracket_prefix(full_title)
        if not artists and bracket_artist:
            artists = [bracket_artist]
        category = meta.get("category", "")
        if category:
            genres.insert(0, category)

        chapter_number = (
            chapter_title.split(" ")[-1]
            if chapter_title.startswith("Chapter ")
            else None
        )

        # Manga and doujinshi read right-to-left; everything else left-to-right.
        reading_direction = (
            "rtl" if category.strip().lower() in ("manga", "doujinshi") else "ltr"
        )
        community_rating: float | None = None
        try:
            raw_rating = float(meta.get("rating") or 0)
        except (TypeError, ValueError):
            raw_rating = 0.0
        # e-hentai rates out of 5; ComicInfo's CommunityRating is out of 10.
        if raw_rating > 0:
            community_rating = round(raw_rating * 2, 2)

        return {
            "base_url": url.rstrip("/") + "/",
            "filecount": filecount,
            "info": ChapterInfo(
                series_title=series_title,
                chapter_title=chapter_title,
                chapter_number=chapter_number,
                total_pages=filecount,
                artists=artists,
                genres=genres,
                language=language,
                reading_direction=reading_direction,
                community_rating=community_rating,
                estimated_size=int(meta.get("filesize") or 0),
            ),
            "source": SourceInfo(
                url=url,
                service="e-hentai",
                user_id=str(gid),
                post_id=token,
            ),
            "cover_url": meta.get("thumb", ""),
        }

    async def _scrape_chapter(
        self, url: str, client: AsyncSession
    ) -> ScrapedChapter:
        skel = await self._gallery_skeleton(url, client)
        images: list[ImageItem] = []
        async for item in _iter_image_items(
            skel["base_url"], skel["filecount"], client
        ):
            images.append(item)

        if not images:
            raise no_images_error()

        return ScrapedChapter(
            info=skel["info"],
            source=skel["source"],
            images=images,
            cover_url=skel["cover_url"],
        )


async def scrape_ehentai(url: str, client: AsyncSession) -> PostMetadata:
    """Scrape an e-hentai gallery through a fresh scraper instance (test helper)."""
    return await EHentaiScraper().scrape(url, client)
