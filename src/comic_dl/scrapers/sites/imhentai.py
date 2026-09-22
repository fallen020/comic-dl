"""IMHentai (imhentai.xxx) gallery scraper."""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from ...models import (
    ChapterInfo,
    ImageItem,
    PostMetadata,
    ScrapedChapter,
    SourceInfo,
    chapter_to_post_metadata,
)
from ..base import (
    BaseScraper,
    _attr_text,
    listing_page_error,
    meta_get,
    meta_index,
    no_images_error,
)
from ..registry import register_scraper

DOMAIN = "imhentai.xxx"
BASE = "https://imhentai.xxx"

_GALLERY_PATH_RE = re.compile(r"^https?://(?:www\.)?imhentai\.xxx/gallery/(\d+)/?$")
_VIEW_PATH_RE = re.compile(r"^https?://(?:www\.)?imhentai\.xxx/view/(\d+)/(\d+)/?$")

_VIEW_LINK_RE = re.compile(r"^/view/(\d+)/(\d+)/?$")

# View pages are tiny static HTML; resolve them a few at a time.
_VIEW_PAGE_SEM = asyncio.Semaphore(4)

_COUNT_SUFFIX_RE = re.compile(r"\d+$")


def is_gallery_url(url: str) -> bool:
    """True when ``url`` points at an IMHentai gallery page."""
    return bool(_GALLERY_PATH_RE.match(url))


def is_view_url(url: str) -> bool:
    """True when ``url`` points at an IMHentai single-page view."""
    return bool(_VIEW_PATH_RE.match(url))


def _gallery_id_from_url(url: str) -> str:
    m = _GALLERY_PATH_RE.match(url) or _VIEW_PATH_RE.match(url)
    return m.group(1) if m else ""


def _view_number(url: str) -> int | None:
    m = _VIEW_PATH_RE.match(url)
    return int(m.group(2)) if m else None


def _clean_tag_name(raw: str) -> str:
    """Tag text carries a trailing gallery count (``dark skin118844``)."""
    return _COUNT_SUFFIX_RE.sub("", raw.strip()).strip()


def _extract_tags(soup: BeautifulSoup) -> list[str]:
    """Tag display names from ``/tag/<slug>/`` anchors (slugs are canonical)."""
    tags: list[str] = []
    seen: set[str] = set()
    for a in soup.select('a[href*="/tag/"]'):
        href = _attr_text(a.get("href"))
        slug = href.rstrip("/").rsplit("/", 1)[-1] if "/tag/" in href else ""
        name = slug.replace("-", " ").strip() or _clean_tag_name(a.get_text(" ", strip=True))
        if name and name.lower() not in seen:
            seen.add(name.lower())
            tags.append(name)
    return tags


def _extract_language(soup: BeautifulSoup, tags: list[str]) -> str:
    for li in soup.select("li"):
        text = li.get_text(strip=True)
        m = re.match(r"^languages?\s*:\s*([a-z]+)", text, re.IGNORECASE)
        if m:
            return m.group(1).lower()
    for tag in tags:
        if tag.lower() in ("english", "japanese", "chinese", "korean", "spanish"):
            return tag.lower()
    return ""


def _extract_view_urls(soup: BeautifulSoup, gallery_id: str) -> list[str]:
    """Ordered ``/view/<id>/<n>/`` URLs, deduplicated (thumbs + nav repeat)."""
    by_page: dict[int, str] = {}
    for a in soup.select("a[href]"):
        m = _VIEW_LINK_RE.match(_attr_text(a.get("href")))
        if m and m.group(1) == gallery_id:
            by_page.setdefault(int(m.group(2)), f"{BASE}/view/{gallery_id}/{m.group(2)}/")
    return [by_page[n] for n in sorted(by_page)]


def _extract_page_count(soup: BeautifulSoup) -> int:
    marker = soup.select_one("input#load_pages")
    if marker is not None:
        try:
            return int(_attr_text(marker.get("value")))
        except (TypeError, ValueError):
            pass
    return 0


def _extract_reader_image(soup: BeautifulSoup) -> str:
    img = soup.select_one("img#gimg")
    src = _attr_text(img.get("src")) if img is not None else ""
    return src


@register_scraper(domain=DOMAIN, capabilities={"chapter"})
class IMHentaiScraper(BaseScraper):
    """IMHentai gallery scraper (one gallery = one chapter)."""

    domain = DOMAIN
    name = "imhentai"
    site_id = "imhentai"
    version = "1.0.0"
    minimum_core_version = "0.0.2"

    def matches_url(self, url: str) -> bool:
        return is_gallery_url(url) or is_view_url(url)

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def _fetch_reader_image(
        self, page_url: str, client: AsyncSession, sem: asyncio.Semaphore
    ) -> str:
        async with sem:
            soup = await self.fetch_html(page_url, client)
            return _extract_reader_image(soup)

    async def _scrape_chapter(self, url: str, client: AsyncSession) -> ScrapedChapter:
        if not is_gallery_url(url) and not is_view_url(url):
            raise listing_page_error("IMHentai", f"{BASE}/gallery/{{id}}/")
        gallery_id = _gallery_id_from_url(url)
        if not gallery_id:
            raise no_images_error()
        soup = await self.fetch_html(f"{BASE}/gallery/{gallery_id}/", client)
        idx = meta_index(soup)

        h1 = soup.select_one("h1")
        title = h1.get_text(strip=True) if h1 else ""
        if not title:
            title_tag = soup.select_one("title")
            title = title_tag.get_text(strip=True).split(" - ")[0] if title_tag else ""
        title = title or f"Gallery {gallery_id}"

        tags = _extract_tags(soup)
        view_urls = _extract_view_urls(soup, gallery_id)
        # The gallery page only thumbnails the first ~10 pages; the full
        # count comes from ``#load_pages`` and remaining ``/view/`` URLs
        # follow the same pattern (verified live).
        have = {_view_number(u) for u in view_urls}
        have.discard(None)
        for n in range(1, _extract_page_count(soup) + 1):
            if n not in have:
                view_urls.append(f"{BASE}/view/{gallery_id}/{n}/")
                have.add(n)
        view_urls.sort(key=lambda u: _view_number(u) or 0)
        if not view_urls:
            raise no_images_error()

        srcs = await asyncio.gather(
            *(self._fetch_reader_image(u, client, _VIEW_PAGE_SEM) for u in view_urls)
        )
        images: list[ImageItem] = []
        for n, (page_url, src) in enumerate(zip(view_urls, srcs, strict=True), start=1):
            if not src:
                continue
            item = ImageItem.from_url(urljoin(page_url, src), n)
            if item is not None:
                images.append(item)
        if not images:
            raise no_images_error()

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=title,
                chapter_title=title,
                description=meta_get(idx, "og:description", "twitter:description"),
                genres=tags,
                language=_extract_language(soup, tags),
                reading_direction="ltr",
                total_pages=len(images),
            ),
            source=SourceInfo(url=url, service=DOMAIN, post_id=gallery_id),
            images=images,
            cover_url=meta_get(idx, "og:image", "twitter:image"),
        )
