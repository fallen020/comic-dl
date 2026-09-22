"""ManhuaTo (manhuato.com) chapter and series scraper."""

from __future__ import annotations

import html
import re
from urllib.parse import quote, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
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
    _attr_text,
    listing_page_error,
    meta_get,
    meta_index,
    no_chapters_error,
    no_images_error,
)
from ..registry import register_scraper

DOMAIN = "manhuato.com"
BASE = "https://manhuato.com"

_SERIES_PATH_RE = re.compile(r"^https?://(?:www\.)?manhuato\.com/(?:manhua|manga)/[a-z0-9-]+/?$")
_CHAPTER_PATH_RE = re.compile(
    r"^https?://(?:www\.)?manhuato\.com/(?:manhua|manga)/[a-z0-9-]+-chapter-\d+-ch\d+/?$"
)

_CHAPTER_SLUG_RE = re.compile(r"-chapter-(\d+)-ch\d+/?$")
_CHAPTER_NUMBER_RE = re.compile(r"chapter\s+(\d+(?:\.\d+)?)", re.IGNORECASE)

_SEO_PREFIX_RE = re.compile(r"^Read\s+.+?\s+at\s+ManhuaTo\.", re.IGNORECASE | re.DOTALL)
_BRIEF_DESC_RE = re.compile(r"^A brief description of the\s+", re.IGNORECASE)
_TYPE_HEADER_RE = re.compile(r"^.+\s+(Manhwa|Manga|Manhua)\s*$", re.IGNORECASE)

_CHAPTER_LIST_SEL = "ul.chapter-list a[href]"
_READER_IMG_SEL = ".chapter-content .item-photo img"


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a ManhuaTo series page (not a chapter)."""
    return bool(_SERIES_PATH_RE.match(url)) and not is_chapter_url(url)


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a ManhuaTo chapter page."""
    return bool(_CHAPTER_PATH_RE.match(url))


def _clean_image_url(raw: str) -> str:
    """Percent-encode the image path (CDN URLs embed the raw series title)."""
    parsed = urlparse(raw.strip())
    return urlunparse(parsed._replace(path=quote(parsed.path)))


def _chapter_number_from_slug(url: str) -> str | None:
    m = _CHAPTER_SLUG_RE.search(url.rstrip("/"))
    return m.group(1) if m else None


def _chapter_number_from_text(text: str) -> str | None:
    m = _CHAPTER_NUMBER_RE.search(text or "")
    return m.group(1) if m else None


def _series_slug_from_url(url: str) -> tuple[str, str]:
    """``(segment, slug)`` for a series or chapter URL (``manhua``/``manga``)."""
    parts = [p for p in urlparse(url).path.rstrip("/").split("/") if p]
    if len(parts) >= 2 and parts[0] in ("manhua", "manga"):
        slug = _CHAPTER_SLUG_RE.sub("", parts[1])
        return parts[0], slug
    return "", ""


def _extract_series_title(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    h1 = soup.select_one("h1")
    if h1 is not None:
        text = html.unescape(h1.get_text(strip=True))
        if text:
            return text
    page_title = meta_get(idx, "og:title", "twitter:title")
    if not page_title:
        title_tag = soup.select_one("title")
        page_title = title_tag.get_text(strip=True) if title_tag else ""
    parts = [p.strip() for p in page_title.split("|") if p.strip()]
    site_name = meta_get(idx, "og:site_name")
    if site_name and parts and parts[-1].lower() == site_name.lower():
        parts = parts[:-1]
    return parts[0] if parts else ""


def _extract_chapter_title(
    soup: BeautifulSoup, idx: dict[str, list[str]], series_title: str
) -> str:
    h1 = soup.select_one("h1")
    candidate = html.unescape(h1.get_text(strip=True)) if h1 else ""
    if not candidate:
        candidate = _extract_series_title(soup, idx)
    if series_title and candidate.startswith(series_title):
        rest = candidate[len(series_title) :].lstrip(" -").strip()
        if rest:
            return rest
    return candidate or series_title


def _clean_description(text: str) -> str:
    """Strip ManhuaTo's SEO boilerplate, keeping the real synopsis.

    ``og:description`` is ``Read <title> ... at ManhuaTo.`` + a header line
    (``A brief description of the manhua <title>:`` or ``<title> Manhwa``)
    + the synopsis, sometimes truncated with ``…`` by the site itself.
    """
    text = _SEO_PREFIX_RE.sub("", text.strip())
    lines = text.splitlines()
    while lines:
        first = lines[0].strip()
        if not first:
            lines.pop(0)
            continue
        if _BRIEF_DESC_RE.match(first):
            lines.pop(0)
            continue
        if len(first) < 120 and _TYPE_HEADER_RE.match(first):
            lines.pop(0)
            continue
        break
    return "\n".join(lines).strip()


def _extract_description(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    return _clean_description(meta_get(idx, "og:description", "twitter:description"))


def _info_rows(soup: BeautifulSoup) -> dict[str, str]:
    """``label -> value`` from the series info ``line-text``/``line-content`` rows."""
    rows: dict[str, str] = {}
    for label in soup.select("span.line-text"):
        name = label.get_text(strip=True).rstrip(":")
        value = label.find_next_sibling("span", class_="line-content")
        if name and value is not None:
            rows[name] = value.get_text(" | ", strip=True)
    return rows


def _extract_genres(rows: dict[str, str]) -> list[str]:
    return [g.strip() for g in rows.get("Genres", "").split("|") if g.strip()]


def _extract_images(soup: BeautifulSoup) -> list[ImageItem]:
    items: list[ImageItem] = []
    seen: set[str] = set()
    for n, img in enumerate(soup.select(_READER_IMG_SEL), start=1):
        src = _attr_text(img.get("src")) or _attr_text(img.get("data-src"))
        if not src or src in seen:
            continue
        if src.startswith("data:"):
            continue
        seen.add(src)
        item = ImageItem.from_url(_clean_image_url(urljoin(BASE, src)), n)
        if item is not None:
            items.append(item)
    return items


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class ManhuatoScraper(BaseScraper):
    """ManhuaTo chapter and series scraper."""

    domain = DOMAIN
    name = "manhuato"
    site_id = "manhuato"
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

    async def _series_page_data(self, segment: str, slug: str, client: AsyncSession) -> dict:
        """Genres/status/description from the series page (best-effort)."""
        try:
            soup = await self.fetch_html(f"{BASE}/{segment}/{slug}", client)
        except Exception:
            return {}
        idx = meta_index(soup)
        rows = _info_rows(soup)
        return {
            "series_title": _extract_series_title(soup, idx),
            "description": _extract_description(soup, idx),
            "cover_url": meta_get(idx, "og:image", "twitter:image"),
            "genres": _extract_genres(rows),
            "status": rows.get("Status") or None,
        }

    async def _scrape_chapter(self, url: str, client: AsyncSession) -> ScrapedChapter:
        if not is_chapter_url(url):
            raise listing_page_error("ManhuaTo", f"{BASE}/manhua/{{series}}/")
        soup, _ = await self.fetch_html_raw(url, client)
        idx = meta_index(soup)

        chapter_number = _chapter_number_from_slug(url)
        series_title = ""
        segment, slug = _series_slug_from_url(url)
        series = await self._series_page_data(segment, slug, client) if slug else {}
        series_title = series.get("series_title", "")
        chapter_title = _extract_chapter_title(soup, idx, series_title)
        if not chapter_number:
            chapter_number = _chapter_number_from_text(chapter_title)
        if not series_title:
            series_title = _extract_series_title(soup, idx)

        # An h1 of "Series - Chapter N" doubles as both titles.
        if chapter_title == series_title and chapter_number:
            chapter_title = f"Chapter {chapter_number}"

        images = _extract_images(soup)
        if not images:
            raise no_images_error()
        cover_url = series.get("cover_url") or meta_get(idx, "og:image", "twitter:image")

        lang = ""
        html_tag = soup.select_one("html")
        if html_tag and html_tag.get("lang"):
            lang = _attr_text(html_tag.get("lang")).split("-")[0].lower()

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=series_title or "Untitled",
                chapter_title=chapter_title or "Chapter",
                chapter_number=chapter_number,
                description=series.get("description", "") or _extract_description(soup, idx),
                genres=series.get("genres", []),
                status=series.get("status"),
                language=lang,
                reading_direction="ltr",
                total_pages=len(images),
            ),
            source=SourceInfo(url=url, service=DOMAIN, post_id=slug or url),
            images=images,
            cover_url=cover_url,
        )

    async def _scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        if not is_series_url(url):
            raise listing_page_error("ManhuaTo", f"{BASE}/manhua/{{series}}/")
        soup = await self.fetch_html(url, client)
        idx = meta_index(soup)

        slug = _series_slug_from_url(url)[1]
        if not slug:
            raise listing_page_error("ManhuaTo", f"{BASE}/manhua/{{series}}/")

        series_title = _extract_series_title(soup, idx)
        chapters: list[dict] = []
        seen_urls: set[str] = set()
        for link in soup.select(_CHAPTER_LIST_SEL):
            href = _attr_text(link.get("href"))
            if not href or href in seen_urls:
                continue
            seen_urls.add(href)
            raw_title = link.get_text(strip=True) or ""
            number = _chapter_number_from_slug(href) or _chapter_number_from_text(raw_title)
            title = raw_title or (f"Chapter {number}" if number else "")
            chapters.append(
                {
                    "title": title,
                    "url": urljoin(url, href),
                    "episode_no": number or title,
                }
            )

        if not chapters:
            raise no_chapters_error()

        chapters.reverse()

        return SeriesMetadata(
            series_title=series_title or slug.replace("-", " ").title(),
            description=_extract_description(soup, idx),
            cover_url=meta_get(idx, "og:image", "twitter:image"),
            title_no=slug,
            chapters=chapters,
        )
