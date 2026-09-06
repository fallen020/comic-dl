"""Pawchive scraper (Patreon, SubscribeStar, Gumroad, Fantia, DLSite archives)."""

from __future__ import annotations

import asyncio

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from ...errors import ScrapeError
from ...models import (
    ChapterInfo,
    ImageItem,
    PostMetadata,
    ScrapedChapter,
    SourceInfo,
    chapter_to_post_metadata,
)
from ...utils import (
    GENERIC_CATEGORIES,
    PART_PATTERN,
    clean_title,
    sanitize_filename,
)
from ..base import (
    BaseScraper,
    _attr_text,
    meta_get,
    meta_index,
)
from ..registry import register_scraper


def _extract_series_and_chapter(
    soup: BeautifulSoup, idx: dict[str, list[str]] | None = None,
) -> tuple[str, str]:
    title_span = soup.select_one("h1.post__title > span")
    full_title = title_span.get_text(strip=True) if title_span else ""

    idx = idx if idx is not None else meta_index(soup)
    series_title = meta_get(idx, "series")
    chapter_title = meta_get(idx, "chapter")
    if series_title and chapter_title:
        return sanitize_filename(series_title), sanitize_filename(chapter_title)

    tag_links = soup.select("section#post-tags a")
    tag_texts = [t.get_text(strip=True).lstrip("#") for t in tag_links]

    if tag_texts:
        non_generic = [t for t in tag_texts if t.lower() not in GENERIC_CATEGORIES]
        if non_generic:
            series_title = non_generic[0]

    if not series_title:
        series_title = clean_title(PART_PATTERN.split(full_title)[0])

    if not series_title:
        series_title = full_title

    part_match = PART_PATTERN.search(full_title)
    if part_match:
        chapter_title = f"Chapter {part_match.group(1)}"
    else:
        chapter_title = full_title if full_title else "Chapter 1"

    return sanitize_filename(series_title), sanitize_filename(chapter_title)


def _extract_images(soup: BeautifulSoup) -> list[ImageItem]:
    imgs = soup.select("div.post__files div.post__thumbnail img")
    items: list[ImageItem] = []
    seen_urls: set[str] = set()
    for fallback_page, img in enumerate(imgs, start=1):
        src = _attr_text(img.get("data-src")) or _attr_text(img.get("src"))
        if not src or src in seen_urls:
            continue
        seen_urls.add(src)
        old = ImageItem.from_url(src, fallback_page)
        page_number = old.page_number if old is not None else fallback_page
        items.append(ImageItem(url=src, page_number=page_number))
    items.sort(key=lambda x: x.page_number)
    return items


def _extract_text_content(soup: BeautifulSoup) -> str:
    """Return readable post-body text, or ``""`` when the post has none.

    Strips image/figure/media nodes so only the author's words remain.
    """
    content = soup.select_one("div.post__content")
    if content is None:
        return ""
    for node in content.select("img, figure, script, style, iframe, video"):
        node.decompose()
    text = content.get_text("\n", strip=True)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _extract_meta(
    soup: BeautifulSoup, idx: dict[str, list[str]] | None = None,
) -> tuple[str, str, str]:
    idx = idx if idx is not None else meta_index(soup)
    return (
        meta_get(idx, "service"),
        meta_get(idx, "user"),
        meta_get(idx, "id"),
    )


async def _try_full_resolution(client: AsyncSession, url: str) -> str:
    if "/thumbnail/" not in url:
        return url
    full_url = url.replace("/thumbnail/", "/")
    try:
        resp = await BaseScraper._timeout_get(full_url, client, method="HEAD")
        if resp.status_code == 200 and "image" in (resp.headers.get("content-type", "")):
            return full_url
    # Fallback probe URL; swallowed failures are fine.
    except Exception:  # nosec B110
        pass
    return url


@register_scraper(domain="pawchive.pw")
class PawchiveScraper(BaseScraper):
    """Pawchive scraper (Patreon/SubscribeStar/Gumroad/Fantia/DLSite posts)."""

    domain = "pawchive.pw"
    name = "pawchive"

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def _scrape_chapter(
        self, url: str, client: AsyncSession,
    ) -> ScrapedChapter:
        soup = await self.fetch_html(url, client)
        idx = meta_index(soup)

        series_title, chapter_title = _extract_series_and_chapter(soup, idx)
        images = _extract_images(soup)
        service, user_id, post_id = _extract_meta(soup, idx)

        if not images:
            pdf_link = soup.select_one('div.post__content a[href$=".pdf"], a[href$=".pdf"]')
            if pdf_link is not None:
                raise ScrapeError(
                    "This post contains a PDF attachment and no images. "
                    "PDF-only posts are not supported by comic-dl.",
                    hint="Download the PDF manually from the post page.",
                )
            text_content = _extract_text_content(soup)
            if text_content:
                return ScrapedChapter(
                    info=ChapterInfo(
                        series_title=series_title,
                        chapter_title=chapter_title,
                    ),
                    source=SourceInfo(
                        url=url,
                        service=service or "pawchive.pw",
                        user_id=user_id,
                        post_id=post_id,
                    ),
                    images=[],
                    cover_url=meta_get(idx, "og:image"),
                    text_content=text_content,
                )
            raise ScrapeError(
                "No images found on this post — it may be private or "
                "require login.",
            )

        if not service:
            service = "pawchive.pw"

        chapter_number = None
        title_span = soup.select_one("h1.post__title > span")
        full_title = title_span.get_text(strip=True) if title_span else ""
        part_match = PART_PATTERN.search(full_title)
        if part_match:
            chapter_number = part_match.group(1)

        lang = ""
        html_tag = soup.select_one("html")
        if html_tag and html_tag.get("lang"):
            lang = _attr_text(html_tag.get("lang")).split("-")[0].lower()

        artists = []
        author_meta = meta_get(idx, "author")
        if author_meta:
            artists = [author_meta]

        cover_url = meta_get(idx, "og:image")

        thumbnail_urls = [img for img in images if "/thumbnail/" in img.url]
        if thumbnail_urls:
            results = await asyncio.gather(
                *[_try_full_resolution(client, img.url) for img in thumbnail_urls]
            )
            for img, new_url in zip(thumbnail_urls, results, strict=False):
                img.url = new_url

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=series_title,
                chapter_title=chapter_title,
                chapter_number=chapter_number,
                language=lang,
                artists=artists,
            ),
            source=SourceInfo(
                url=url,
                service=service,
                user_id=user_id,
                post_id=post_id,
            ),
            images=images,
            cover_url=cover_url,
        )


async def scrape_post(url: str, client: AsyncSession) -> PostMetadata:
    """Scrape a pawchive post through a fresh scraper instance (test helper)."""
    scraper = PawchiveScraper()
    return await scraper.scrape(url, client)
