"""Tapas (tapas.io) series and free-episode scraper.

Tapas is an official licensed platform: series pages are server-rendered
but the episode list loads via a same-origin JSON endpoint, and reader
pages carry signed image URLs. Only free episodes are downloadable —
locked episodes need login/Ink, so the series listing skips them and a
single locked-episode URL fails with a login hint instead of images.
"""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urljoin

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

DOMAIN = "tapas.io"
BASE = "https://tapas.io"

_SERIES_PATH_RE = re.compile(r"^https?://(?:www\.)?tapas\.io/series/[A-Za-z0-9-]+/?$")
_EPISODE_PATH_RE = re.compile(r"^https?://(?:www\.)?tapas\.io/episode/(\d+)/?$")

_SERIES_ID_RE = re.compile(r'data-series-id="(\d+)"')
_SERIES_ID_FALLBACK_RE = re.compile(r"seriesId\s*[:=]\s*\"?(\d+)")

_READER_IMG_SEL = "article.viewer__body img.content__img"

_EPISODES_PAGE_SIZE = 20


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a Tapas series page."""
    return bool(_SERIES_PATH_RE.match(url))


def is_episode_url(url: str) -> bool:
    """True when ``url`` points at a Tapas episode page."""
    return bool(_EPISODE_PATH_RE.match(url))


def _episode_id_from_url(url: str) -> str:
    m = _EPISODE_PATH_RE.match(url)
    return m.group(1) if m else ""


def _series_id_from_series_html(raw: str) -> str:
    """Numeric series id: most frequent ``data-series-id`` (sidebar recs differ)."""
    counts = Counter(_SERIES_ID_RE.findall(raw))
    if counts:
        return counts.most_common(1)[0][0]
    m = _SERIES_ID_FALLBACK_RE.search(raw)
    return m.group(1) if m else ""


def _extract_synopsis(soup: BeautifulSoup) -> str:
    """Series synopsis (``og:description`` is site boilerplate, not the story)."""
    node = soup.select_one(".js-series-description")
    return node.get_text(" ", strip=True) if node is not None else ""


def _series_slug_from_episode(soup: BeautifulSoup) -> str:
    """Series slug from an episode page (for description enrichment).

    Prefers the reader header's ``/series/<slug>/info`` link; recommendation
    cards link other series, so a bare first-match would misattribute.
    """
    fallback = ""
    for a in soup.select('a[href*="/series/"]'):
        href = _attr_text(a.get("href"))
        if href.rstrip("/").endswith("/info"):
            return href.rstrip("/").rsplit("/", 2)[-2]
        if not fallback and _SERIES_PATH_RE.match(urljoin(BASE, href)):
            fallback = href.rstrip("/").rsplit("/", 1)[-1]
    return fallback


def _is_free(episode: object) -> bool:
    """Free episodes need no login; locked ones need Ink/payment."""
    if not isinstance(episode, dict):
        return False
    if episode.get("must_pay"):
        return False
    return bool(episode.get("free"))


def _extract_reader_images(soup: BeautifulSoup, base: str) -> list[ImageItem]:
    items: list[ImageItem] = []
    seen: set[str] = set()
    for n, img in enumerate(soup.select(_READER_IMG_SEL), start=1):
        src = _attr_text(img.get("data-src")) or _attr_text(img.get("src"))
        if not src or src in seen or src.startswith("data:"):
            continue
        seen.add(src)
        item = ImageItem.from_url(urljoin(base, src), n)
        if item is not None:
            items.append(item)
    return items


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class TapasScraper(BaseScraper):
    """Tapas series and free-episode scraper."""

    domain = DOMAIN
    name = "tapas"
    site_id = "tapas"
    version = "1.0.0"
    minimum_core_version = "0.0.2"

    def matches_url(self, url: str) -> bool:
        return is_episode_url(url) or is_series_url(url)

    def matches_series_url(self, url: str) -> bool:
        return is_series_url(url)

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        return await self._scrape_series(url, client)

    async def _episode_list(self, series_id: str, client: AsyncSession) -> list[dict]:
        """All episodes, oldest first (paginated JSON, 20 per page)."""
        episodes: list[dict] = []
        page = 1
        while True:
            resp = await BaseScraper._timeout_get(
                f"{BASE}/series/{series_id}/episodes?page={page}",
                client,
                expect_json=True,
            )
            resp.raise_for_status()
            try:
                body = resp.json()
            except ValueError:
                break
            data = body.get("data", {}) if isinstance(body, dict) else {}
            batch = data.get("episodes", []) if isinstance(data, dict) else []
            if not isinstance(batch, list) or not batch:
                break
            episodes.extend(e for e in batch if isinstance(e, dict))
            pagination = data.get("pagination", {}) if isinstance(data, dict) else {}
            if not pagination.get("has_next", False):
                break
            page += 1
        return episodes

    async def _scrape_chapter(self, url: str, client: AsyncSession) -> ScrapedChapter:
        if not is_episode_url(url):
            raise listing_page_error("Tapas", f"{BASE}/series/{{slug}}/")
        episode_id = _episode_id_from_url(url)
        if not episode_id:
            raise no_images_error()
        soup = await self.fetch_html(url, client)
        idx = meta_index(soup)

        images = _extract_reader_images(soup, url)
        if not images:
            raise no_images_error(
                hint="Free episodes only — this episode may require login or Ink.",
            )

        header = soup.select_one("div.viewer__header > p.title")
        chapter_title = header.get_text(strip=True) if header else ""
        series_anchor = soup.select_one("p.center-info__title")
        series_title = series_anchor.get_text(strip=True) if series_anchor else ""
        if not series_title:
            page_title = soup.select_one("title")
            text = page_title.get_text(strip=True) if page_title else ""
            m = re.match(r"Read\s+(.+?)\s*::", text)
            series_title = m.group(1) if m else meta_get(idx, "og:site_name")

        description = ""
        series_slug = _series_slug_from_episode(soup)
        if series_slug:
            try:
                series_soup = await self.fetch_html(f"{BASE}/series/{series_slug}", client)
                description = _extract_synopsis(series_soup)
            except Exception:
                description = ""

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=series_title or "Tapas",
                chapter_title=chapter_title or f"Episode {episode_id}",
                description=description,
                language="en",
                reading_direction="ltr",
                total_pages=len(images),
            ),
            source=SourceInfo(url=url, service=DOMAIN, post_id=episode_id),
            images=images,
            cover_url=meta_get(idx, "og:image", "twitter:image"),
        )

    async def _scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        if not is_series_url(url):
            raise listing_page_error("Tapas", f"{BASE}/series/{{slug}}/")
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        if not slug:
            raise listing_page_error("Tapas", f"{BASE}/series/{{slug}}/")
        soup, raw = await self.fetch_html_raw(url, client)
        idx = meta_index(soup)

        series_id = _series_id_from_series_html(raw)
        if not series_id:
            raise no_chapters_error()
        title_node = soup.select_one("p.center-info__title")
        series_title = title_node.get_text(strip=True) if title_node else ""
        if not series_title:
            series_title = meta_get(idx, "og:title").split("|")[0].strip() or slug

        chapters: list[dict] = []
        for entry in await self._episode_list(series_id, client):
            if not _is_free(entry):
                continue
            episode_id = entry.get("id")
            if episode_id is None:
                continue
            title = str(entry.get("title") or "").strip() or f"Episode {episode_id}"
            chapters.append(
                {
                    "title": title,
                    "url": f"{BASE}/episode/{episode_id}",
                    "episode_no": title,
                }
            )

        if not chapters:
            raise no_chapters_error()

        return SeriesMetadata(
            series_title=series_title,
            description=_extract_synopsis(soup)
            or meta_get(idx, "og:description", "twitter:description"),
            cover_url=meta_get(idx, "og:image", "twitter:image"),
            title_no=slug,
            chapters=chapters,
        )
