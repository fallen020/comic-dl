"""LGBTics (lgbtics.com) scraper for its Madara-style reader."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..base import _attr_text, meta_get, meta_index
from ..madara import (
    MadaraSeriesSiteScraper,
    cover_from_meta,
    extract_lang,
    extract_meta_rows,
    extract_post_id,
    reader_images,
    rows_first_prefixed,
)
from ..registry import register_scraper

DOMAIN = "lgbtics.com"
BASE = "https://lgbtics.com"

_SERIES_PATH_RE = re.compile(r"^https?://(?:www\.)?lgbtics\.com/comic/[^/]+/?$")
_CHAPTER_PATH_RE = re.compile(r"^https?://(?:www\.)?lgbtics\.com/comic/[^/]+/.+/?$")

# Matches numbers in chapter slugs like "1-secretary-chapter-1-scara2b" or "chapter-25"
_CHAPTER_NUM_RE = re.compile(r"(?:^|[-/])(\d+(?:\.\d+)?)(?:[-/]|$)", re.IGNORECASE)

_CHAPTER_LIST_SEL = ".version-chap .wp-manga-chapter a[href]"
_READ_CONTAINERS = (".reading-content",)


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a series page for this source."""
    return bool(_SERIES_PATH_RE.match(url))


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a chapter/gallery page for this source."""
    return bool(_CHAPTER_PATH_RE.match(url))


def _on_image_host(raw: str) -> bool:
    host = urlparse(raw).hostname or ""
    return host.lower() == DOMAIN


def _extract_series_slug(url: str) -> str:
    parts = [p for p in url.rstrip("/").split("/") if p]
    try:
        i = parts.index("comic")
    except ValueError:
        return ""
    return parts[i + 1] if i + 1 < len(parts) else ""


def _chapter_number_from_url(url: str) -> str | None:
    m = _CHAPTER_NUM_RE.search(url)
    return m.group(1) if m else None


def _extract_series_title(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    """Series title from the first ``h1`` in .post-title, else OpenGraph title."""
    h1 = soup.select_one(".post-title h1")
    if h1 is not None:
        text = h1.get_text(strip=True)
        if text:
            return text

    page_title = meta_get(idx, "og:title", "twitter:title")
    if not page_title:
        title_tag = soup.select_one("title")
        page_title = title_tag.get_text(strip=True) if title_tag else ""
    parts = [p.strip() for p in page_title.split(" - ") if p.strip()]
    if parts and parts[-1].lower().startswith("lgbtics"):
        parts = parts[:-1]
    if parts:
        return parts[0]
    return ""


def _extract_authors(soup: BeautifulSoup) -> list[str]:
    return rows_get(extract_meta_rows(soup), "artists", "author(s)")


def _extract_genres(soup: BeautifulSoup) -> list[str]:
    rows = extract_meta_rows(soup)
    # lgbtics uses "genres" key instead of "genre(s)"
    genres = rows_get(rows, "genres", "genre(s)")
    tags = rows_get(rows, "tag(s)")
    return list(dict.fromkeys(genres + tags))


def _extract_status(soup: BeautifulSoup) -> str | None:
    return rows_first_prefixed(extract_meta_rows(soup), "status")


def _extract_images(soup: BeautifulSoup) -> list:
    return reader_images(soup, _READ_CONTAINERS, _on_image_host)


def rows_get(rows: dict[str, list[str]], *labels: str) -> list[str]:
    """Values of the first row whose key matches any of ``labels``."""
    for label in labels:
        values = rows.get(label.lower())
        if values:
            return values
    return []


# Re-exported helpers (tests import them by these names).
_extract_meta_rows = extract_meta_rows
_extract_lang = extract_lang
_extract_post_id = extract_post_id


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class LgbticsScraper(MadaraSeriesSiteScraper):
    """LGBTics chapter and series scraper."""

    domain = DOMAIN
    name = "lgbtics"
    site_id = "lgbtics"
    version = "1.0.0"
    minimum_core_version = "0.0.2"
    base_url = BASE
    series_segment = "comic"
    # lgbtics chapter URLs don't use a consistent "chapter-" prefix
    chapter_path_marker = ""

    series_url_re = _SERIES_PATH_RE
    chapter_url_re = _CHAPTER_PATH_RE
    chapter_list_selector = _CHAPTER_LIST_SEL
    reader_containers = _READ_CONTAINERS
    chapter_number_re = _CHAPTER_NUM_RE

    def _image_host_ok(self, raw: str) -> bool:
        return _on_image_host(raw)

    def _series_title_from_page(self, soup, idx) -> str:
        return _extract_series_title(soup, idx)

    def _series_page_url(self, slug: str) -> str:
        return f"{BASE}/comic/{slug}/"

    def _parse_series_page(self, soup: BeautifulSoup) -> dict:
        idx = meta_index(soup)
        return {
            "series_title": _extract_series_title(soup, idx),
            "description": self._series_summary(soup, idx),
            "cover_url": cover_from_meta(idx),
            "authors": _extract_authors(soup),
            "genres": _extract_genres(soup),
            "status": _extract_status(soup),
        }

    async def _chapter_links(
        self,
        soup: BeautifulSoup,
        client,
        series_page_url: str,
    ) -> list[tuple[str, str]]:
        """Extract chapter links without filtering by chapter_path_marker."""
        links: list[tuple[str, str]] = []
        seen: set[str] = set()
        for link in soup.select(self.chapter_list_selector):
            href = _attr_text(link.get("href"))
            if not href or href in seen:
                continue
            seen.add(href)
            links.append((href, link.get_text(strip=True) or ""))
        return links
