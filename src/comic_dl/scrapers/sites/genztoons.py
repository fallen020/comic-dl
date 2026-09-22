"""GenzToons (genztoons.org) scraper.

Series pages (``/series/{slug}/``) statically render the chapter list in
``#chapters`` plus the title, synopsis, cover, and Author/Artist/Type/Status
cards. Chapter pages (``/chapter/{uid}/``) render reader pages as placeholder
``img`` tags carrying ``uid`` attributes; the real URL is rebuilt against
``cdn.meowing.org``. Best-effort series-page enrichment supplies the fields
the reader page omits.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
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
    listing_page_error,
    meta_get,
    meta_index,
    no_chapters_error,
    no_images_error,
)
from ..registry import register_scraper

DOMAIN = "genztoons.org"
BASE = "https://genztoons.org"

_UPLOAD_BASE = "https://cdn.meowing.org/uploads/"
# Mirror host serves the same reader; canonical domain stays genztoons.org.
_SITE_HOST = r"(?:www\.)?genztoons\.(?:org|net)"

_SERIES_PATH_RE = re.compile(rf"^https?://{_SITE_HOST}/series/[^/]+/?$")

_CHAPTER_PATH_RE = re.compile(rf"^https?://{_SITE_HOST}/chapter/[^/]+/?$")

_CHAPTER_NUM_RE = re.compile(r"Chapter\s*(\d+(?:\.\d+)?)", re.IGNORECASE)

# Reader uids are opaque content hashes with an extension (e.g. 65b3675e7d7.avif);
# reject anything that could path-traverse or inject a foreign URL.
_UID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z0-9]+$")

_READER_SEL = "#pages"

_STAT_LABELS = ("Author", "Artist", "Type", "Status")

# Trailing site-marketing suffix on series descriptions (same line or next).
_BOILERPLATE_SUFFIX_RE = re.compile(r"(?:\n\s*|\s+)-\s*A Standard scanlation.*$", re.DOTALL)

_LOCKED_CHAPTER_RE = re.compile(r"early access chapter", re.IGNORECASE)


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a series page for this source."""
    return bool(_SERIES_PATH_RE.match(url))


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a chapter/gallery page for this source."""
    return bool(_CHAPTER_PATH_RE.match(url))


def _series_slug_from_url(url: str) -> str:
    parts = [p for p in url.rstrip("/").split("/") if p]
    try:
        i = parts.index("series")
    except ValueError:
        return ""
    return parts[i + 1] if i + 1 < len(parts) else ""


def _chapter_number_from_title(title: str) -> str | None:
    m = _CHAPTER_NUM_RE.search(title)
    return m.group(1) if m else None


def _extract_series_title(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    h1 = soup.select_one("h1")
    if h1 is not None:
        text = h1.get_text(" ", strip=True)
        # A chapter anchor inside the series h1 means the page is a reader; the
        # bare h1 text on a series page is the series title itself.
        if text and h1.select_one("a[href*='/series/']") is None:
            return text
    return meta_get(idx, "og:title", "twitter:title")


def _extract_description(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    """Series synopsis without the trailing site-marketing suffix."""
    return _BOILERPLATE_SUFFIX_RE.sub(
        "", meta_get(idx, "og:description", "twitter:description", "description")
    ).strip()


def _extract_cover(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    content = meta_get(idx, "og:image", "twitter:image")
    return content.split(",")[0].strip() if content else ""


def _stat_cards(soup: BeautifulSoup) -> list[tuple[str, Tag]]:
    """``(label, card)`` pairs for the series metadata cards.

    Each card is a ``div.grid`` inside the ``flex`` wrapper holding one labeled
    chip; walking the wrapper keeps a stray top-level label from matching.
    """
    cards: list[tuple[str, Tag]] = []
    for card in soup.select("div.flex.w-full.flex-wrap > div.grid"):
        label_el: Tag | None = card.select_one("span")
        if label_el is None:
            continue
        label = label_el.get_text(strip=True)
        if label in _STAT_LABELS:
            cards.append((label, card))
    return cards


def _stat_value(card: Tag) -> str:
    text = card.get_text(" ", strip=True)
    label_el = card.select_one("span")
    label = label_el.get_text(strip=True) if label_el is not None else ""
    if text.startswith(label):
        text = text[len(label) :].strip()
    return text


def _extract_stats(soup: BeautifulSoup) -> dict[str, str]:
    return {label: _stat_value(card) for label, card in _stat_cards(soup) if _stat_value(card)}


def _extract_genres(soup: BeautifulSoup) -> list[str]:
    """Genre names with quote/comma punctuation stripped.

    Anchors render like ``'Action,'`` (quoted with a trailing comma), which
    would otherwise join into ``Action,, Adventure`` downstream.
    """
    genres = []
    for a in soup.select('a[href*="/series/?genre="]'):
        name = a.get_text(" ", strip=True).strip("'\"").rstrip(",").strip()
        if name:
            genres.append(name)
    return genres


def _split_names(value: str) -> list[str]:
    """Comma-separated card value (``Glump, Northwood``) into names."""
    return [part.strip() for part in value.split(",") if part.strip()]


def _is_locked_chapter(soup: BeautifulSoup) -> bool:
    """Early-access paywall with no reader images (sign in + purchase)."""
    return bool(_LOCKED_CHAPTER_RE.search(soup.get_text(" ", strip=True)))


def _extract_images(soup: BeautifulSoup) -> list[ImageItem]:
    images: list[ImageItem] = []
    seen: set[str] = set()

    scope = soup.select_one(_READER_SEL)
    if scope is None:
        return images
    for img in scope.find_all("img", class_="myImage"):
        uid = _attr_text(img.get("uid"))
        if not uid or uid in seen or not _UID_RE.match(uid):
            continue
        seen.add(uid)
        images.append(ImageItem(url=_UPLOAD_BASE + uid, page_number=len(images) + 1))

    return images


def _extract_header(
    soup: BeautifulSoup,
    idx: dict[str, list[str]],
) -> tuple[str, str, str]:
    """``(series_title, series_slug, chapter_title)`` from the reader page.

    The reader header anchors back to the series, so the chapter's series
    identity and the series-page slug both come from that one anchor.
    """
    series_title = ""
    series_slug = ""
    anchor = soup.select_one('h1 a[href*="/series/"]')
    if anchor is not None:
        series_title = _attr_text(anchor.get("title"))
        series_slug = _series_slug_from_url(_attr_text(anchor.get("href")))

    head = meta_get(idx, "og:title", "twitter:title") or ""
    h1 = soup.select_one("h1")
    if h1 is not None:
        head = h1.get_text(" ", strip=True)
    if series_title and head.startswith(series_title):
        head = head[len(series_title) :].lstrip(" -:").strip()
    return series_title, series_slug, head


def _extract_lang(soup: BeautifulSoup) -> str:
    html_tag = soup.select_one("html")
    if html_tag and html_tag.get("lang"):
        return _attr_text(html_tag.get("lang")).split("-")[0].lower()
    return ""


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class GenzToonsScraper(BaseScraper):
    """GenzToons chapter and series scraper."""

    domain = DOMAIN
    name = "genztoons"
    site_id = "genztoons"
    version = "1.0.0"
    minimum_core_version = "0.0.2"

    def __init__(self) -> None:
        super().__init__()
        self._series_cache: dict[str, dict] = {}

    def matches_url(self, url: str) -> bool:
        return is_chapter_url(url) or is_series_url(url)

    def matches_series_url(self, url: str) -> bool:
        return is_series_url(url)

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        return await self._scrape_series(url, client)

    async def _series_page_data(self, series_slug: str, client: AsyncSession) -> dict:
        """Fetch the series page once per slug (cached on the instance).

        The reader page omits authors/genres/status/description; those live only
        on the series page. Best-effort — a failed fetch degrades to empty
        enrichment.
        """
        cached = self._series_cache.get(series_slug)
        if cached is not None:
            return cached
        data: dict = {}
        try:
            response = await BaseScraper._timeout_get(f"{BASE}/series/{series_slug}/", client)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            idx = meta_index(soup)
            stats = _extract_stats(soup)
            genres = _extract_genres(soup)
            if stats.get("Type") and stats["Type"] not in genres:
                genres.append(stats["Type"])
            data = {
                "series_title": _extract_series_title(soup, idx),
                "description": _extract_description(soup, idx),
                "cover_url": _extract_cover(soup, idx),
                "genres": genres,
                "authors": _split_names(stats.get("Author", "")),
                "artists": _split_names(stats.get("Artist", "")),
                "status": stats.get("Status"),
            }
        # Enrichment is best-effort.
        except Exception:  # nosec B110
            pass
        self._series_cache[series_slug] = data
        return data

    async def _scrape_chapter(
        self,
        url: str,
        client: AsyncSession,
    ) -> ScrapedChapter:
        if not is_chapter_url(url):
            raise listing_page_error("GenzToons", f"{BASE}/series/{{slug}}/")
        soup, _ = await BaseScraper.fetch_html_raw(url, client)
        idx = meta_index(soup)

        images = _extract_images(soup)
        if not images:
            if _is_locked_chapter(soup):
                raise ScrapeError(
                    "This is an early access chapter.",
                    hint="Sign in and purchase it in a browser, then run again — "
                    "locked chapters need an unlocked session.",
                )
            raise no_images_error()

        series_title, series_slug, chapter_title = _extract_header(soup, idx)
        series = await self._series_page_data(series_slug, client) if series_slug else {}

        series_title = series_title or series.get("series_title", "")
        chapter_number = _chapter_number_from_title(chapter_title)
        if not chapter_title:
            chapter_title = f"Chapter {chapter_number}" if chapter_number else "Chapter"

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=series_title or "Untitled",
                chapter_title=chapter_title or "Chapter",
                chapter_number=chapter_number,
                description=series.get("description", ""),
                authors=series.get("authors", []),
                artists=series.get("artists", []),
                genres=series.get("genres", []),
                status=series.get("status"),
                language=_extract_lang(soup) or "en",
                reading_direction="ltr",
                total_pages=len(images),
            ),
            source=SourceInfo(url=url, service=DOMAIN),
            images=images,
            cover_url=series.get("cover_url", ""),
        )

    async def _scrape_series(
        self,
        url: str,
        client: AsyncSession,
    ) -> SeriesMetadata:
        if not is_series_url(url):
            raise listing_page_error("GenzToons", f"{BASE}/series/{{slug}}/")
        soup, _ = await BaseScraper.fetch_html_raw(url, client)
        idx = meta_index(soup)

        series_title = _extract_series_title(soup, idx)
        description = _extract_description(soup, idx)
        cover_url = _extract_cover(soup, idx)
        title_no = _series_slug_from_url(url)

        panel = soup.select_one("#chapters")
        if panel is None:
            raise no_chapters_error()

        chapters: list[dict] = []
        seen_urls: set[str] = set()
        for link in panel.select('a[href*="/chapter/"]'):
            href = _attr_text(link.get("href"))
            if not href or href in seen_urls or not href.startswith("/chapter/"):
                continue
            title = _attr_text(link.get("title")) or link.get_text(" ", strip=True)
            number = _chapter_number_from_title(title)
            if number is None:
                continue
            seen_urls.add(href)
            chapters.append(
                {
                    "title": title,
                    "url": urljoin(url, href),
                    "episode_no": number,
                }
            )

        if not chapters:
            raise no_chapters_error()

        def _sort_key(item: dict) -> float:
            try:
                return float(item["episode_no"])
            except (TypeError, ValueError):
                return float("inf")

        chapters.sort(key=_sort_key)

        return SeriesMetadata(
            series_title=series_title
            or (title_no.replace("-", " ").title() if title_no else "Untitled"),
            description=description,
            cover_url=cover_url,
            title_no=title_no,
            chapters=chapters,
        )
