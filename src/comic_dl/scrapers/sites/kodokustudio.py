"""KodokuStudio (kodokustudio.com) scraper for its Madara-style reader.

KodokuStudio is a Madara desktop theme with two quirks: chapter URLs use the
Portuguese ``capitulo-{n}`` segment, and the series page renders only "Read
First"/"Read Last" links — the full chapter list loads from the Madara
``ajax/chapters`` endpoint. The reader is otherwise standard: series metadata
lives on the series page, chapter images render inline in ``.reading-content``
on WordPress's ``i*.wp.com`` CDN proxy.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..base import BaseScraper, _attr_text, meta_index
from ..madara import (
    MadaraSeriesSiteScraper,
    extract_meta_rows,
    reader_images,
    rows_first_prefixed,
)
from ..registry import register_scraper

DOMAIN = "kodokustudio.com"
BASE = "https://kodokustudio.com"

_SERIES_PATH_RE = re.compile(
    r"^https?://(?:www\.)?kodokustudio\.com/manhua/[^/]+/?$"
)
_CHAPTER_PATH_RE = re.compile(
    r"^https?://(?:www\.)?kodokustudio\.com/manhua/[^/]+/capitulo-[^/]+/?$"
)

_CHAPTER_NUM_RE = re.compile(r"capitulo-([0-9.]+)", re.IGNORECASE)

# The series page renders only the first/last chapter links; the lazy chapter
# list is fetched from this endpoint (the ``t`` page parameter is ignored by
# the server and always returns the full newest-first list).
_CHAPTERS_ENDPOINT = "ajax/chapters/?t=1"

_CHAPTER_LIST_SEL = ".wp-manga-chapter a[href]"
_READ_CONTAINERS = (".reading-content",)

# Chapter images are served through WordPress's Photon CDN proxy.
_IMAGE_HOST_RE = re.compile(r"^i\d\.wp\.com$", re.IGNORECASE)


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a series page for this source."""
    return bool(_SERIES_PATH_RE.match(url))


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a chapter/gallery page for this source."""
    return bool(_CHAPTER_PATH_RE.match(url))


def _on_image_cdn(raw: str) -> bool:
    host = (urlparse(raw).hostname or "").lower()
    return bool(_IMAGE_HOST_RE.match(host)) or host == DOMAIN


def _extract_series_slug(url: str) -> str:
    parts = [p for p in url.rstrip("/").split("/") if p]
    try:
        i = parts.index("manhua")
    except ValueError:
        return ""
    return parts[i + 1] if i + 1 < len(parts) else ""


def _chapter_number_from_url(url: str) -> str | None:
    m = _CHAPTER_NUM_RE.search(url)
    return m.group(1) if m else None


def _extract_series_title(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    """Series title from the first ``h1``."""
    h1 = soup.select_one("h1")
    if h1 is not None:
        text = h1.get_text(strip=True)
        if text:
            return text
    return ""


def _extract_status(soup: BeautifulSoup) -> str | None:
    return rows_first_prefixed(extract_meta_rows(soup), "status")


def _extract_images(soup: BeautifulSoup) -> list:
    return reader_images(soup, _READ_CONTAINERS, _on_image_cdn)


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class KodokuStudioScraper(MadaraSeriesSiteScraper):
    """KodokuStudio chapter and series scraper."""

    domain = DOMAIN
    name = "kodokustudio"
    base_url = BASE
    series_segment = "manhua"
    chapter_path_marker = "capitulo-"

    series_url_re = _SERIES_PATH_RE
    chapter_url_re = _CHAPTER_PATH_RE
    chapter_list_selector = _CHAPTER_LIST_SEL
    reader_containers = _READ_CONTAINERS
    chapter_number_re = _CHAPTER_NUM_RE

    def _image_host_ok(self, raw: str) -> bool:
        return _on_image_cdn(raw)

    def _series_title_from_page(self, soup, idx):
        return _extract_series_title(soup, idx)

    def _series_cover(self, soup, idx) -> str:
        # The only og:image on series pages is the site logo; the page
        # renders no real cover, so report none rather than the logo.
        return ""

    async def _chapter_links(self, soup, client, series_page_url):
        """Fetch the lazy chapter list from the Madara ajax endpoint."""
        response = await BaseScraper._timeout_get(
            f"{series_page_url}{_CHAPTERS_ENDPOINT}",
            client,
            method="POST",
            use_cache=False,
        )
        response.raise_for_status()
        list_soup = BeautifulSoup(response.text, "lxml")
        links = []
        seen = set()
        for link in list_soup.select(_CHAPTER_LIST_SEL):
            href = _attr_text(link.get("href"))
            if not href or href in seen:
                continue
            seen.add(href)
            links.append((href, link.get_text(strip=True) or ""))
        return links

    def _parse_series_page(self, soup: BeautifulSoup) -> dict:
        idx = meta_index(soup)
        return {
            "series_title": _extract_series_title(soup, idx),
            "description": "",
            "cover_url": "",
            "authors": [],
            "genres": [],
            "status": _extract_status(soup),
        }
