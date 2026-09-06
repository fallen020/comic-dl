"""Shared plumbing for Madara/WordPress comic readers.

gedecomix, manhwaz, toonily and kodokustudio run the same Madara theme family:
a summary box (``post-content_item`` rows) on the series page, a lazy-loaded
reader container for chapter images, ``postid-<n>`` body classes, and a series
page carrying the metadata a chapter page omits. The helpers here encode that
structure once; each site module keeps only its selectors, URL grammar, and
title heuristics.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from ..models import (
    ChapterInfo,
    ImageItem,
    PostMetadata,
    ScrapedChapter,
    SeriesMetadata,
    SourceInfo,
    chapter_to_post_metadata,
)
from ..ui import DIAGNOSTIC, TAG_SCRAPE, vlog
from .base import (
    BaseScraper,
    _attr_text,
    meta_get,
    meta_index,
    no_chapters_error,
    no_images_error,
)

# WordPress archive/taxonomy pages carry these exact body classes; single
# comic/chapter posts carry `single` + `postid-<n>` plus prefixed classes
# like `tax-wp-manga-tag-<slug>`, which never match as exact tokens.
ARCHIVE_BODY_CLASSES = frozenset({
    "archive", "category", "tag", "tax", "term", "search", "error404",
})

_POSTID_CLASS_RE = re.compile(r"\bpostid-(\d+)")

# WordPress generates scaled copies as ``image-800x1067.jpg``; stripping the
# suffix fetches the full-resolution original instead of the thumbnail.
_WORDPRESS_RESIZE_RE = re.compile(r"-\d+x\d+(?=\.\w+$)")

_LAZY_SRC_ATTRS = ("data-src", "data-lazy-src", "src")


def _body_classes(soup: BeautifulSoup) -> set[str]:
    body = soup.select_one("body")
    if body is None:
        return set()
    return {
        str(c) for c in (body.get("class") or []) if isinstance(c, str)
    }


def is_archive_page(soup: BeautifulSoup) -> bool:
    """True when the page is a category/tag/archive listing, not a comic."""
    return bool(_body_classes(soup) & ARCHIVE_BODY_CLASSES)


def extract_post_id(soup: BeautifulSoup) -> str:
    """WordPress post id from the ``postid-<n>`` body class (``''`` if absent)."""
    body = soup.select_one("body")
    if body is not None:
        m = _POSTID_CLASS_RE.search(" ".join(_body_classes(soup)))
        if m:
            return m.group(1)
    return ""


def extract_lang(soup: BeautifulSoup) -> str:
    """Primary subtag of the document's ``<html lang>`` (``''`` if absent)."""
    html_tag = soup.select_one("html")
    if html_tag and html_tag.get("lang"):
        return _attr_text(html_tag.get("lang")).split("-")[0].lower()
    return ""


def clean_image_url(raw: str, *, strip_resize: bool = False) -> str:
    """Drop query/fragment, optionally also the WordPress ``-WxH`` suffix."""
    clean = raw.split("?")[0].split("#")[0]
    if strip_resize:
        clean = _WORDPRESS_RESIZE_RE.sub("", clean)
    return clean


def cover_from_meta(idx: dict[str, list[str]]) -> str:
    """First OpenGraph/Twitter cover image, query/fragment-stripped."""
    content = meta_get(idx, "og:image", "twitter:image")
    if content:
        return clean_image_url(content.split(",")[0].strip())
    return ""


def extract_meta_rows(soup: BeautifulSoup) -> dict[str, list[str]]:
    """Madara summary box rows as ``{label.lower(): [values]}``.

    Multi-link rows (``Genre(s)``/``Author(s)``) keep one entry per link;
    scalar rows fall back to comma-split text.
    """
    rows: dict[str, list[str]] = {}
    for item in soup.select(".post-content_item"):
        label_el = item.select_one("h5, .item-label, .post-content-section")
        if label_el is None:
            continue
        label = label_el.get_text(strip=True).rstrip(":").strip().lower()
        value_el = item.select_one(".summary-content")
        if value_el is None:
            continue
        values = [
            a.get_text(strip=True)
            for a in value_el.select("a")
            if a.get_text(strip=True)
        ]
        if not values:
            raw = value_el.get_text(separator=",", strip=True)
            values = [v.strip() for v in raw.split(",") if v.strip()]
        if label and values:
            rows[label] = values
    return rows


def rows_get(rows: dict[str, list[str]], *labels: str) -> list[str]:
    """Values of the first row whose key matches any of ``labels``."""
    for label in labels:
        values = rows.get(label.lower())
        if values:
            return values
    return []


def rows_first_prefixed(
    rows: dict[str, list[str]], prefix: str,
) -> str | None:
    """First value of the first row whose key starts with ``prefix``.

    Madara sites spell labels inconsistently (``Status``, ``Status(s)``);
    prefix matching absorbs that without per-site tables.
    """
    for key, values in rows.items():
        if key.startswith(prefix):
            return values[0] if values else None
    return None


def genres_from_rows(rows: dict[str, list[str]]) -> list[str]:
    """Genre(s) merged with Tag(s), deduped in source order."""
    genres: list[str] = []
    genres.extend(rows_get(rows, "genre(s)"))
    genres.extend(rows_get(rows, "tag(s)"))
    return list(dict.fromkeys(genres))


def reader_images(
    soup: BeautifulSoup,
    containers: tuple[str, ...],
    cdn_ok,
    *,
    strip_resize: bool = False,
) -> list[ImageItem]:
    """Chapter images from the first matching reader container.

    Tries each selector in ``containers`` in order, falling back to the
    whole document when none match. Picks up lazy-load ``data-src``/
    ``data-lazy-src`` variants, skips inline data URIs, keeps only URLs
    passing ``cdn_ok`` (the site's image-host policy), dedupes after
    cleaning, and numbers pages from 1 in document order.
    """
    images: list[ImageItem] = []
    seen: set[str] = set()

    scope = None
    for sel in containers:
        scope = soup.select_one(sel)
        if scope is not None:
            break
    if scope is None:
        scope = soup

    for img in scope.find_all("img"):
        src = ""
        for attr in _LAZY_SRC_ATTRS:
            candidate = _attr_text(img.get(attr))
            if candidate:
                src = candidate
                break
        if not src or src.startswith("data:"):
            continue
        if not cdn_ok(src):
            continue

        clean = clean_image_url(src, strip_resize=strip_resize)
        if clean in seen:
            continue
        seen.add(clean)
        images.append(ImageItem(url=clean, page_number=len(images) + 1))

    return images


class MadaraScraper(BaseScraper):
    """Base for Madara readers: cached best-effort series-page enrichment.

    Chapter pages on Madara sites omit authors/genres/status; subclasses
    implement :meth:`_parse_series_page` to pull those fields from the
    series page, which is fetched once per slug and cached on the instance.
    A failed enrichment degrades to empty metadata — never aborts the
    chapter scrape — but is logged at DIAGNOSTIC level so layout changes
    are visible under ``-vv``.
    """

    domain = ""
    name = ""
    base_url = ""
    # Path segment introducing the series slug, e.g. "porncomic".
    series_segment = ""

    def __init__(self) -> None:
        super().__init__()
        self._series_cache: dict[str, dict] = {}

    def _series_slug(self, url: str) -> str:
        parts = [p for p in url.rstrip("/").split("/") if p]
        try:
            i = parts.index(self.series_segment)
        except ValueError:
            return ""
        return parts[i + 1] if i + 1 < len(parts) else ""

    def _series_page_url(self, slug: str) -> str:
        """Series-page URL for ``slug``; override for odd canonical forms."""
        return f"{self.base_url}/{self.series_segment}/{slug}/"

    def _parse_series_page(self, soup: BeautifulSoup) -> dict:
        raise NotImplementedError

    async def _series_page_data(
        self, slug: str, client: AsyncSession,
    ) -> dict:
        cached = self._series_cache.get(slug)
        if cached is not None:
            return cached
        data: dict = {}
        try:
            response = await BaseScraper._timeout_get(
                self._series_page_url(slug), client,
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            data = self._parse_series_page(soup) or {}
        # Enrichment is best-effort.
        except Exception as exc:  # nosec B110
            vlog(
                DIAGNOSTIC,
                f"series enrichment unavailable for {slug}: "
                f"{type(exc).__name__}",
                tag=TAG_SCRAPE,
            )
        self._series_cache[slug] = data
        return data


_SERIES_SUMMARY_SEL = ".summary__content"


class MadaraSeriesSiteScraper(MadaraScraper):
    """A Madara reader with the standard series + chapter page flow.

    manhwaz, toonily and kodokustudio differ from each other only in URL
    grammar, reader selectors, image-host policy, the series-title heuristic,
    and which metadata rows the series page exposes. Those stay in the site
    modules; the chapter/series scrape orchestration lives here once instead
    of being duplicated per site.
    """

    #: Regexes recognizing this site's series and chapter URLs (used by
    #: :meth:`matches_url` and the module-level ``is_*_url`` helpers).
    series_url_re: re.Pattern[str] | None = None
    chapter_url_re: re.Pattern[str] | None = None

    #: URL path segment (without the trailing number) linking a series to its
    #: chapters, e.g. ``"chapter-"`` for ``.../chapter-12/``. Sites whose
    #: grammar uses another word (``capitulo-``) override this.
    chapter_path_marker = "chapter-"

    #: CSS selector for the chapter-list links on a series page.
    chapter_list_selector = ""

    #: Reader containers tried in order on a chapter page.
    reader_containers: tuple[str, ...] = ()

    #: Number captured from a chapter URL's ``chapter-N`` segment.
    chapter_number_re: re.Pattern[str] | None = None

    def matches_url(self, url: str) -> bool:
        return bool(
            (self.series_url_re is not None and self.series_url_re.match(url))
            or (self.chapter_url_re is not None and self.chapter_url_re.match(url))
        )

    def _image_host_ok(self, raw: str) -> bool:
        """True when ``raw`` points at this site's chapter-image host."""
        raise NotImplementedError

    def _series_title_from_page(self, soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
        """Series title from its page — a per-site heuristic."""
        raise NotImplementedError

    def _series_summary(self, soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
        """Series blurb from the standard Madara summary box, else meta tags.

        Both manhwaz and toonily expose the summary the same way, so the
        fallback chain lives here.
        """
        summary = soup.select_one(_SERIES_SUMMARY_SEL)
        if summary is not None:
            text = summary.get_text(" ", strip=True)
            if text:
                return text
        return meta_get(idx, "og:description", "twitter:description", "description")

    def _series_meta_fields(self, series: dict) -> dict:
        """Series-data keys shared by both sites, as ``ChapterInfo`` args."""
        return {
            "description": series.get("description", ""),
            "authors": series.get("authors", []),
            "genres": series.get("genres", []),
            "status": series.get("status"),
        }

    def _chapter_number_from_url(self, url: str) -> str | None:
        if self.chapter_number_re is None:
            return None
        m = self.chapter_number_re.search(url)
        return m.group(1) if m else None

    async def _chapter_links(
        self, soup: BeautifulSoup, client: AsyncSession, series_page_url: str,
    ) -> list[tuple[str, str]]:
        """``(href, label)`` pairs for the series chapter list.

        The default reads the chapter list rendered in the series page
        ``soup``. Subclasses on lazy themes load the list from the Madara
        ``ajax/chapters`` endpoint instead; ``client`` and ``series_page_url``
        exist for exactly that override.
        """
        links: list[tuple[str, str]] = []
        seen: set[str] = set()
        for link in soup.select(self.chapter_list_selector):
            href = _attr_text(link.get("href"))
            if not href or href in seen:
                continue
            seen.add(href)
            links.append((href, link.get_text(strip=True) or ""))
        return links

    def _series_cover(self, soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
        """Series cover URL — a per-site override point.

        The default uses the OpenGraph/Twitter image; sites whose meta tags
        only carry a site logo (no real cover) override this.
        """
        return cover_from_meta(idx)

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        return await self._scrape_series(url, client)

    async def _scrape_chapter(
        self, url: str, client: AsyncSession,
    ) -> ScrapedChapter:
        soup, _ = await self.fetch_html_raw(url, client)

        images = reader_images(soup, self.reader_containers, self._image_host_ok)
        if not images:
            raise no_images_error()

        series_slug = self._series_slug(url)
        series = await self._series_page_data(series_slug, client)

        series_title = series.get("series_title") or ""
        h1 = soup.select_one("h1")
        chapter_title = ""
        if h1 is not None:
            h1_text = h1.get_text(strip=True)
            if h1_text:
                chapter_title = h1_text
        if series_title and chapter_title.startswith(series_title):
            chapter_title = chapter_title[len(series_title):].lstrip(" -").strip()
        if not chapter_title:
            title_tag = soup.select_one("title")
            chapter_title = title_tag.get_text(strip=True) if title_tag else ""

        info: dict[str, Any] = {
            "series_title": series_title or "Untitled",
            "chapter_title": chapter_title or "Chapter",
            "chapter_number": self._chapter_number_from_url(url),
            "language": extract_lang(soup) or "en",
            "reading_direction": "ltr",
            "total_pages": len(images),
        }
        info.update(self._series_meta_fields(series))

        return ScrapedChapter(
            info=ChapterInfo(**info),
            source=SourceInfo(
                url=url, service=self.domain, post_id=extract_post_id(soup)
            ),
            images=images,
            cover_url=series.get("cover_url", ""),
        )

    async def _scrape_series(
        self, url: str, client: AsyncSession,
    ) -> SeriesMetadata:
        soup = await self.fetch_html(url, client)
        idx = meta_index(soup)

        series_slug = self._series_slug(url)
        series_title = self._series_title_from_page(soup, idx)
        description = self._series_summary(soup, idx)
        cover_url = self._series_cover(soup, idx)

        chapters: list[dict] = []
        seen_urls: set[str] = set()

        marker = f"/{self.series_segment}/{series_slug}/{self.chapter_path_marker}"
        for href, raw_title in await self._chapter_links(
            soup, client, self._series_page_url(series_slug),
        ):
            if href in seen_urls:
                continue
            number = self._chapter_number_from_url(href)
            if number is None:
                continue
            if marker not in href:
                continue
            seen_urls.add(href)
            chapters.append({
                "title": raw_title or f"Chapter {number}",
                "url": urljoin(url, href),
                "episode_no": number,
            })

        if not chapters:
            raise no_chapters_error()

        chapters.reverse()

        return SeriesMetadata(
            series_title=series_title or series_slug.replace("-", " ").title(),
            description=description,
            cover_url=cover_url,
            title_no=series_slug,
            chapters=chapters,
        )
