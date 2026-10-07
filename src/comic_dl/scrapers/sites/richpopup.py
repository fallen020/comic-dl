"""RichPopup (richpopup.com) gallery scraper.

DataLife Engine site serving standalone galleries: each post is
``/{id}-{slug}.html`` with every page image inline as a same-origin
``/comic/<hash>/<n>.webp`` (lazy-loaded via ``data-src``/``data-original``).
No series pages exist, so this source is chapter-only.
"""

from __future__ import annotations

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

DOMAIN = "richpopup.com"
BASE = "https://richpopup.com"

_POST_PATH_RE = re.compile(r"^https?://(?:www\.)?richpopup\.com/\d+-[a-z0-9-]+\.html/?$")
_POST_ID_RE = re.compile(r"richpopup\.com/(\d+)-")
_TITLE_SUFFIX_RE = re.compile(r"\s*»\s*RichPopUp\.com Porn Comics\s*$", re.IGNORECASE)

# Page images live in the article body. Sidebar TOP thumbs reuse the same
# ``/comic/`` path with a ``/thumb/`` segment, so the scope (not the host)
# is what keeps them out.
_READER_SEL = "article.rp-article .rp-desc, article.rp-article .title_spoiler"


def is_gallery_url(url: str) -> bool:
    """True when ``url`` points at a RichPopup gallery post."""
    return bool(_POST_PATH_RE.match(url))


def _extract_title(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    h1 = soup.select_one(".rp-title h1")
    if h1 is not None and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    return _TITLE_SUFFIX_RE.sub("", meta_get(idx, "og:title", "twitter:title")).strip()


def _extract_post_id(url: str) -> str:
    m = _POST_ID_RE.search(url)
    return m.group(1) if m else ""


def _extract_artists(soup: BeautifulSoup) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for a in soup.select('a[href*="/artauthor/"]'):
        name = a.get_text(strip=True)
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def _extract_tags(soup: BeautifulSoup, idx: dict[str, list[str]]) -> list[str]:
    tags = [a.get_text(strip=True) for a in soup.select(".rp-tag a") if a.get_text(strip=True)]
    if not tags:
        keywords = meta_get(idx, "news_keywords")
        tags = [t.strip() for t in keywords.split(",") if t.strip()]
    return list(dict.fromkeys(tags))


def _extract_images(soup: BeautifulSoup, base: str) -> list[ImageItem]:
    scope = soup.select_one(_READER_SEL)
    if scope is None:
        return []
    images: list[ImageItem] = []
    seen: set[str] = set()
    for img in scope.find_all("img"):
        src = (
            _attr_text(img.get("data-src"))
            or _attr_text(img.get("data-original"))
            or _attr_text(img.get("src"))
        )
        if not src or src.startswith("data:") or "/thumb/" in src:
            continue
        url = urljoin(base, BaseScraper.clean_image_url(src))
        if url in seen:
            continue
        seen.add(url)
        images.append(ImageItem(url=url, page_number=len(images) + 1))
    return images


@register_scraper(domain=DOMAIN, capabilities={"chapter"})
class RichPopupScraper(BaseScraper):
    """RichPopup gallery scraper."""

    domain = DOMAIN
    name = "richpopup"
    site_id = "richpopup"
    version = "1.0.0"
    test_url = "https://richpopup.com/29038-immoral-desires-12.html"
    test_url_kind = "chapter"
    minimum_core_version = "0.0.2"

    def matches_url(self, url: str) -> bool:
        return is_gallery_url(url)

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def _scrape_chapter(self, url: str, client: AsyncSession) -> ScrapedChapter:
        if not is_gallery_url(url):
            raise listing_page_error("RichPopup", f"{BASE}/{{id}}-{{slug}}.html")
        soup, _ = await self.fetch_html_raw(url, client)
        idx = meta_index(soup)

        title = _extract_title(soup, idx) or "Untitled"
        images = _extract_images(soup, url)
        if not images:
            raise no_images_error()

        lang = ""
        html_tag = soup.select_one("html")
        if html_tag and html_tag.get("lang"):
            lang = _attr_text(html_tag.get("lang")).split("-")[0].lower()

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=title,
                chapter_title=title,
                description=meta_get(idx, "og:description", "twitter:description"),
                genres=_extract_tags(soup, idx),
                artists=_extract_artists(soup),
                language=lang,
                reading_direction="ltr",
                total_pages=len(images),
            ),
            source=SourceInfo(url=url, service=DOMAIN, post_id=_extract_post_id(url) or url),
            images=images,
            cover_url=meta_get(idx, "og:image", "twitter:image") or images[0].url,
        )
