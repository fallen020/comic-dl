"""Toonily (toonily.com) scraper for its Madara/WordPress reader."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..base import meta_get, meta_index
from ..madara import (
    MadaraSeriesSiteScraper,
    clean_image_url,
    cover_from_meta,
    extract_lang,
    extract_meta_rows,
    extract_post_id,
    genres_from_rows,
    reader_images,
    rows_first_prefixed,
    rows_get,
)
from ..registry import register_scraper

DOMAIN = "toonily.com"
BASE = "https://toonily.com"

_SERIES_PATH_RE = re.compile(
    r"^https?://(?:www\.)?toonily\.com/serie/[^/]+/?$"
)
_CHAPTER_PATH_RE = re.compile(
    r"^https?://(?:www\.)?toonily\.com/serie/[^/]+/chapter-[^/]+/?$"
)

_CHAPTER_NUM_RE = re.compile(r"chapter-([0-9.]+)", re.IGNORECASE)

_CHAPTER_LIST_SEL = ".listing-chapters_wrap a[href]"
_READ_CONTAINERS = (".reading-content", ".read-container")

# Chapter images live on Toonily's own CDN; logos, avatars and related-manga
# thumbnails are outside the reading container or on other hosts.
_IMAGE_HOST_SUFFIXES = (".tnlycdn.com", "tnlycdn.com")


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a series page for this source."""
    return bool(_SERIES_PATH_RE.match(url))


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a chapter/gallery page for this source."""
    return bool(_CHAPTER_PATH_RE.match(url))


def _clean_image_url(raw: str) -> str:
    return clean_image_url(raw)


def _on_image_cdn(raw: str) -> bool:
    host = (urlparse(raw).hostname or "").lower()
    return host.endswith(_IMAGE_HOST_SUFFIXES)


def _extract_series_slug(url: str) -> str:
    parts = [p for p in url.rstrip("/").split("/") if p]
    try:
        i = parts.index("serie")
    except ValueError:
        return ""
    return parts[i + 1] if i + 1 < len(parts) else ""


def _chapter_number_from_url(url: str) -> str | None:
    m = _CHAPTER_NUM_RE.search(url)
    return m.group(1) if m else None


def _extract_series_title(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    """Series title from the ``.post-title h1`` (badges stripped), else the
    OpenGraph title's first segment."""
    h1 = soup.select_one(".post-title h1")
    if h1 is not None:
        for badge in h1.select(".manga-title-badges"):
            badge.decompose()
        text = h1.get_text(strip=True)
        if text:
            return text

    page_title = meta_get(idx, "og:title", "twitter:title")
    if not page_title:
        title_tag = soup.select_one("title")
        page_title = title_tag.get_text(strip=True) if title_tag else ""
    parts = [p.strip() for p in page_title.split(" - ") if p.strip()]
    if parts and parts[-1].lower().startswith("toonily"):
        parts = parts[:-1]
    if parts and "Read" in parts[0]:
        parts = parts[1:]
    if parts:
        return parts[0]
    return ""


def _extract_artists(soup: BeautifulSoup) -> list[str]:
    return rows_get(extract_meta_rows(soup), "artist(s)")


def _extract_genres(soup: BeautifulSoup) -> list[str]:
    return genres_from_rows(extract_meta_rows(soup))


def _extract_status(soup: BeautifulSoup) -> str | None:
    return rows_first_prefixed(extract_meta_rows(soup), "status")


def _extract_publisher(soup: BeautifulSoup) -> str | None:
    return rows_first_prefixed(extract_meta_rows(soup), "publisher")


def _extract_rating(soup: BeautifulSoup) -> float | None:
    rating = soup.select_one("#averagerate")
    if rating is None:
        return None
    text = rating.get_text(strip=True)
    try:
        return float(text)
    except ValueError:
        return None


def _extract_images(soup: BeautifulSoup) -> list:
    return reader_images(soup, _READ_CONTAINERS, _on_image_cdn)


# Re-exported helpers (tests import them by these names).
_extract_meta_rows = extract_meta_rows
_extract_lang = extract_lang
_extract_post_id = extract_post_id


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class ToonilyScraper(MadaraSeriesSiteScraper):
    """Toonily chapter and series scraper."""

    domain = DOMAIN
    name = "toonily"
    base_url = BASE
    series_segment = "serie"

    series_url_re = _SERIES_PATH_RE
    chapter_url_re = _CHAPTER_PATH_RE
    chapter_list_selector = _CHAPTER_LIST_SEL
    reader_containers = _READ_CONTAINERS
    chapter_number_re = _CHAPTER_NUM_RE

    def _image_host_ok(self, raw: str) -> bool:
        return _on_image_cdn(raw)

    def _series_title_from_page(self, soup, idx) -> str:
        return _extract_series_title(soup, idx)

    def _series_meta_fields(self, series: dict) -> dict:
        """Toonily exposes artists (not authors), publisher, and rating."""
        return {
            "description": series.get("description", ""),
            "artists": series.get("artists", []),
            "genres": series.get("genres", []),
            "status": series.get("status"),
            "publisher": series.get("publisher"),
            "community_rating": series.get("community_rating"),
        }

    def _parse_series_page(self, soup: BeautifulSoup) -> dict:
        idx = meta_index(soup)
        return {
            "series_title": _extract_series_title(soup, idx),
            "description": self._series_summary(soup, idx),
            "cover_url": cover_from_meta(idx),
            "artists": _extract_artists(soup),
            "genres": _extract_genres(soup),
            "status": _extract_status(soup),
            "publisher": _extract_publisher(soup),
            "community_rating": _extract_rating(soup),
        }
