"""GEDE Comix (Madara/WordPress) scraper."""

from __future__ import annotations

import html
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from ...models import (
    ChapterInfo,
    PostMetadata,
    ScrapedChapter,
    SeriesMetadata,
    SourceInfo,
    chapter_to_post_metadata,
)
from ..base import (
    BaseScraper,
    _attr_text,
    article_jsonld_nodes,
    listing_page_error,
    meta_get,
    meta_index,
    no_chapters_error,
    no_images_error,
)
from ..madara import (
    MadaraScraper,
    clean_image_url,
    extract_meta_rows,
    genres_from_rows,
    is_archive_page,
    reader_images,
    rows_first_prefixed,
    rows_get,
)
from ..madara import (
    extract_lang as _madara_extract_lang,
)
from ..madara import (
    extract_post_id as _madara_extract_post_id,
)
from ..registry import register_scraper, url_in_domain

DOMAIN = "gedecomix.com"
BASE = "https://gedecomix.com"

_SERIES_PATH_RE = re.compile(
    r"^https?://(?:www\.)?gedecomix\.com/porncomic/[^/]+/?$"
)

_CHAPTER_PATH_RE = re.compile(
    r"^https?://(?:www\.)?gedecomix\.com/porncomic/[^/]+/[^/]+/?$"
)

_CHAPTER_NUMBER_RE = re.compile(
    r"(?:chapter|ch)[.\s]*#?\s*(\d+)", re.IGNORECASE
)

_CHAPTER_NUM_PREFIX_RE = re.compile(r"^\d+[\s.\-]*")

_READ_CONTAINER_SEL = ".read-container"

_CHAPTER_LIST_SEL = ".listing-chapters_wrap a[href]"


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a series page for this source."""
    return bool(_SERIES_PATH_RE.match(url))


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a chapter/gallery page for this source."""
    return bool(_CHAPTER_PATH_RE.match(url))


def _clean_image_url(raw: str) -> str:
    return clean_image_url(raw, strip_resize=True)


def _get_image_ext(url: str) -> str:
    return BaseScraper.image_ext(url)


def _extract_post_id(soup: BeautifulSoup) -> str:
    return _madara_extract_post_id(soup)


def _extract_lang(soup: BeautifulSoup) -> str:
    return _madara_extract_lang(soup)


def _extract_artists(soup: BeautifulSoup) -> list[str]:
    return rows_get(extract_meta_rows(soup), "artist(s)")


def _extract_genres(soup: BeautifulSoup) -> list[str]:
    return genres_from_rows(extract_meta_rows(soup))


def _extract_status(soup: BeautifulSoup) -> str | None:
    values = rows_get(extract_meta_rows(soup), "status")
    return values[0] if values else None


def _extract_images(soup: BeautifulSoup) -> list:
    return reader_images(
        soup,
        (_READ_CONTAINER_SEL,),
        lambda raw: url_in_domain(raw, DOMAIN),
        strip_resize=True,
    )


def _extract_chapter_number(text: str) -> str | None:
    m = _CHAPTER_NUMBER_RE.search(text)
    if m:
        return m.group(1)
    return None


def _chapter_number_from_slug(url: str) -> str | None:
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    m = re.match(r"^(\d+)", slug)
    if m:
        return m.group(1)
    return None


def _series_slug_from_url(url: str) -> str:
    parts = [p for p in url.rstrip("/").split("/") if p]
    try:
        i = parts.index("porncomic")
    except ValueError:
        return ""
    if i + 2 < len(parts):
        return parts[i + 1]
    return ""


def _series_headline(soup: BeautifulSoup) -> str:
    """JSON-LD ``Article.headline`` — the authoritative, dash-preserving
    series title when present on GEDE Comix pages."""
    for node in article_jsonld_nodes(soup):
        headline = node.get("headline")
        if isinstance(headline, str) and headline.strip():
            return html.unescape(headline.strip())
    return ""


def _site_stripped_title(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    """OpenGraph/page title with the trailing ``- GEDE Comix`` marker removed."""
    page_title = meta_get(idx, "og:title", "twitter:title")
    if not page_title:
        title_tag = soup.select_one("title")
        page_title = html.unescape(title_tag.get_text(strip=True)) if title_tag else ""
    parts = [p.strip() for p in page_title.split(" - ") if p.strip()]
    site_name = meta_get(idx, "og:site_name")
    if site_name and parts and parts[-1] == site_name:
        parts = parts[:-1]
    else:
        domain_lower = DOMAIN.replace(".com", "").lower()
        if parts and parts[-1].lower().startswith(domain_lower):
            parts = parts[:-1]
    return " - ".join(parts)


def _extract_series_title(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    """Series title from JSON-LD ``headline``, else the title's first segment.

    The JSON-LD headline is preferred because multi-word series titles such as
    ``Hell Village - My Sweet Seduction`` contain `` - `` and would be broken
    in half by a naive ``split(" - ")`` of the page title.
    """
    headline = _series_headline(soup)
    if headline:
        return headline

    stripped = _site_stripped_title(soup, idx)
    parts = [p.strip() for p in stripped.split(" - ") if p.strip()]
    if parts and parts[-1] == "Porn Comic":
        parts = parts[:-1]
    if parts:
        return parts[0]
    return ""


def _extract_chapter_title(
    soup: BeautifulSoup,
    idx: dict[str, list[str]],
    series_title: str,
) -> str:
    """Chapter title by stripping the known series prefix off the page title."""
    stripped = _site_stripped_title(soup, idx)
    if series_title and stripped.startswith(series_title):
        rest = stripped[len(series_title):].lstrip(" -").strip()
        if rest:
            return rest
    if stripped:
        return stripped
    return series_title


def _extract_titles(soup: BeautifulSoup, idx: dict[str, list[str]]) -> tuple[str, str]:
    """Series + chapter title for a chapter/reading page."""
    series_title = _extract_series_title(soup, idx)
    chapter_title = _extract_chapter_title(soup, idx, series_title)
    return series_title, chapter_title


def _extract_description(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    """JSON-LD ``description`` when present, else the OpenGraph description."""
    for node in article_jsonld_nodes(soup):
        description = node.get("description")
        if isinstance(description, str) and description.strip():
            return description.strip()
    return meta_get(idx, "og:description", "twitter:description", "description")


def _extract_cover(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    content = meta_get(idx, "og:image", "twitter:image")
    if content:
        return _clean_image_url(content.split(",")[0].strip())
    return ""


def _extract_year(soup: BeautifulSoup) -> int | None:
    for node in article_jsonld_nodes(soup):
        raw = node.get("datePublished")
        if isinstance(raw, str):
            m = re.match(r"^(\d{4})", raw.strip())
            if m:
                return int(m.group(1))
    return None


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class GedecomixScraper(MadaraScraper):
    """GEDE Comix chapter and series scraper."""

    domain = DOMAIN
    name = "gedecomix"
    base_url = BASE
    series_segment = "porncomic"

    def matches_url(self, url: str) -> bool:
        return is_chapter_url(url) or is_series_url(url)

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        return await self._scrape_series(url, client)

    def _parse_series_page(self, soup: BeautifulSoup) -> dict:
        # A slug that resolves to a tag/category archive yields no enrichment;
        # the chapter scrape still succeeds with page-local metadata.
        if is_archive_page(soup):
            return {}
        rows = extract_meta_rows(soup)
        return {
            "artists": rows_get(rows, "artist(s)"),
            "genres": genres_from_rows(rows),
            "status": rows_first_prefixed(rows, "status"),
        }

    async def _scrape_chapter(
        self, url: str, client: AsyncSession,
    ) -> ScrapedChapter:
        soup, _ = await self.fetch_html_raw(url, client)
        idx = meta_index(soup)

        if is_archive_page(soup):
            raise listing_page_error(
                "GEDE Comix",
                f"{BASE}/porncomic/{{series}}/{{chapter}}/",
            )

        series_title, chapter_title = _extract_titles(soup, idx)
        h1 = soup.select_one("h1")
        if h1 is not None:
            h1_text = h1.get_text(strip=True)
            if h1_text and h1_text != series_title:
                chapter_title = h1_text

        if not chapter_title:
            title_tag = soup.select_one("title")
            chapter_title = title_tag.get_text(strip=True) if title_tag else ""

        description = _extract_description(soup, idx)
        cover_url = _extract_cover(soup, idx)
        images = _extract_images(soup)

        if not images:
            raise no_images_error()

        chapter_number = (
            _extract_chapter_number(chapter_title)
            or _chapter_number_from_slug(url)
        )

        artists: list[str] = []
        genres: list[str] = []
        status: str | None = None
        series_slug = _series_slug_from_url(url)
        if series_slug:
            series = await self._series_page_data(series_slug, client)
            artists = series.get("artists", [])
            genres = series.get("genres", [])
            status = series.get("status")

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=series_title or "Untitled",
                chapter_title=chapter_title or "Chapter",
                chapter_number=chapter_number,
                description=description,
                artists=artists,
                genres=genres,
                status=status,
                language=_extract_lang(soup),
                reading_direction="ltr",
                year=_extract_year(soup),
                total_pages=len(images),
            ),
            source=SourceInfo(url=url, service=DOMAIN, post_id=_extract_post_id(soup)),
            images=images,
            cover_url=cover_url,
        )

    async def _scrape_series(
        self, url: str, client: AsyncSession,
    ) -> SeriesMetadata:
        soup = await self.fetch_html(url, client)

        if is_archive_page(soup):
            raise listing_page_error(
                "GEDE Comix",
                f"{BASE}/porncomic/{{series}}/",
            )

        idx = meta_index(soup)
        series_title = _extract_series_title(soup, idx)
        description = _extract_description(soup, idx)
        cover_url = _extract_cover(soup, idx)

        title_no = url.rstrip("/").rsplit("/", 1)[-1]

        chapters: list[dict] = []
        seen_urls: set[str] = set()

        for link in soup.select(_CHAPTER_LIST_SEL):
            href = _attr_text(link.get("href"))
            if not href or href in seen_urls:
                continue
            seen_urls.add(href)
            raw_title = link.get_text(strip=True) or ""
            title = _CHAPTER_NUM_PREFIX_RE.sub("", raw_title).strip() or raw_title
            number = _chapter_number_from_slug(href) or _extract_chapter_number(raw_title)
            chapters.append({
                "title": title,
                "url": urljoin(url, href),
                "episode_no": number or title,
            })

        if not chapters:
            raise no_chapters_error()

        chapters.reverse()

        return SeriesMetadata(
            series_title=series_title or title_no.replace("-", " ").title(),
            description=description,
            cover_url=cover_url,
            title_no=title_no,
            chapters=chapters,
        )
