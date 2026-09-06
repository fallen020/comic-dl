"""FSIComics scraper."""

from __future__ import annotations

import asyncio
import json
import re
from html import unescape
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
from ...utils import sanitize_filename
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

DOMAIN = "fsicomics.com"
BASE = "https://fsicomics.com"

_SERIES_PATH_RE = re.compile(
    r"^https?://(?:www\.)?fsicomics\.com/all-porn-comics/.+"
)

_CHAPTER_PATH_RE = re.compile(
    r"^https?://(?:www\.)?fsicomics\.com/"
    r"(?!all-porn-comics|wp-|feed|page|porn-comics-video|ai-generated|"
    r"contact-us|privacy-policy|terms|about|search)[^/]+/?$"
)

_VALID_EXTS = frozenset({"jpg", "jpeg", "png", "webp", "gif", "bmp"})

_WORDPRESS_RESIZE_RE = re.compile(r"-\d+x\d+(?=\.\w+$)")

_CHAPTER_NUMBER_RE = re.compile(
    r"(?:chapter|ch)[.\s]*#?\s*(\d+)", re.IGNORECASE
)

_CHAPTER_MARKER_RE = re.compile(r"-chapter-", re.IGNORECASE)

_CHAPTER_NUM_PREFIX_RE = re.compile(r"^\d+[\s._-]*")

_EN_DASH = "\u2013"


def _derive_series_title(url: str, chapter_title: str) -> str:
    """Guess the series directory for a chapter URL.

    ``family-debt-chapter-1-traplust`` with title ``Family Debt Chapter 1``
    (and a ``- TRAPLust`` artist tail) yields ``Family Debt - TRAPLust``.
    Artist casing is taken from the title when it matches the slug's artist.
    Returns ``""`` when the slug has no ``-chapter-`` marker, so callers keep
    the page metadata as-is.
    """
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    parts = _CHAPTER_MARKER_RE.split(slug, maxsplit=1)
    if len(parts) < 2:
        return ""
    series = parts[0].replace("-", " ").strip().title()

    artist = _CHAPTER_NUM_PREFIX_RE.sub("", parts[1]).replace("-", " ").strip().title()
    title_artist = ""
    for sep in (f"{_EN_DASH} ", " - "):
        if sep in chapter_title:
            candidate = chapter_title.rsplit(sep, 1)[-1].strip()
            if candidate:
                title_artist = candidate
                break
    if title_artist and (not artist or title_artist.lower() == artist.lower()):
        artist = title_artist

    if not artist:
        return series
    return f"{series} {_EN_DASH} {artist}"


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a series page for this source."""
    return bool(_SERIES_PATH_RE.match(url))


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a chapter/gallery page for this source."""
    return bool(_CHAPTER_PATH_RE.match(url))


# WordPress archive/taxonomy pages carry these exact body classes. A single
# comic post never does — it has `single` and `postid-<n>` plus prefixed
# classes like `category-<slug>`/`tag-<slug>`, which won't match because the
# set is checked as exact whitespace-delimited tokens.
_ARCHIVE_BODY_CLASSES = frozenset({
    "archive", "category", "tag", "tax", "term", "search", "error404",
})


def _is_archive_page(soup: BeautifulSoup) -> bool:
    """True when the page is a category/tag/archive listing, not a comic."""
    body = soup.select_one("body")
    if body is None:
        return False
    classes = {
        str(c) for c in (body.get("class") or []) if isinstance(c, str)
    }
    return bool(classes & _ARCHIVE_BODY_CLASSES)


def _clean_image_url(raw: str) -> str:
    clean = raw.split("?")[0].split("#")[0]
    clean = _WORDPRESS_RESIZE_RE.sub("", clean)
    return clean


def _get_image_ext(url: str) -> str:
    path = url.split("?")[0]
    try:
        _, ext = path.rsplit(".", 1)
    except ValueError:
        return "jpg"
    ext = ext.lower()
    return ext if ext in _VALID_EXTS else "jpg"


def _extract_images(soup: BeautifulSoup) -> list[ImageItem]:
    images: list[ImageItem] = []
    seen: set[str] = set()

    entry = soup.select_one(".entry-content")
    if not entry:
        entry = soup

    for img in entry.find_all("img"):
        src = (
            _attr_text(img.get("data-src"))
            or _attr_text(img.get("data-lazy-src"))
            or _attr_text(img.get("src"))
        )
        if not src or src.startswith("data:"):
            continue
        if "wp-content/uploads/" not in src:
            continue

        clean = _clean_image_url(src)
        if clean in seen:
            continue
        seen.add(clean)
        images.append(ImageItem(url=clean, page_number=len(images) + 1))

    return images


def _extract_meta(soup: BeautifulSoup, idx: dict[str, list[str]] | None = None) -> tuple[str, str]:
    idx = idx if idx is not None else meta_index(soup)
    title_tag = soup.select_one("title")
    page_title = title_tag.get_text(strip=True) if title_tag else ""

    if not page_title:
        page_title = meta_get(idx, "og:title")

    site_name = meta_get(idx, "og:site_name")

    parts = [p.strip() for p in page_title.split(" - ") if p.strip()]
    if site_name and parts and parts[-1] == site_name:
        parts = parts[:-1]
    else:
        domain_lower = DOMAIN.replace(".com", "").lower()
        if parts and parts[-1].lower().startswith(domain_lower):
            parts = parts[:-1]

    if len(parts) >= 2:
        chapter_title = parts[0]
        series_title = parts[1]
    elif len(parts) == 1:
        chapter_title = parts[0]
        series_title = parts[0]
    else:
        chapter_title = ""
        series_title = ""

    if not series_title:
        series_title = meta_get(idx, "article:section")

    return series_title or "Untitled", chapter_title or page_title


def _extract_description(soup: BeautifulSoup, idx: dict[str, list[str]] | None = None) -> str:
    idx = idx if idx is not None else meta_index(soup)
    return meta_get(idx, "og:description", "twitter:description", "description")


def _extract_cover(soup: BeautifulSoup, idx: dict[str, list[str]] | None = None) -> str:
    idx = idx if idx is not None else meta_index(soup)
    content = meta_get(idx, "og:image", "twitter:image")
    if content:
        return _clean_image_url(content.split(",")[0].strip())
    return ""


def _extract_chapter_number(title: str) -> str | None:
    m = _CHAPTER_NUMBER_RE.search(title)
    if m:
        return m.group(1)
    return None


_POSTID_CLASS_RE = re.compile(r"\bpostid-(\d+)")
_POST_ID_ATTR_RE = re.compile(r'id="post-(\d+)"')


def _extract_post_id(soup: BeautifulSoup, raw_html: str | None = None) -> str:
    body = soup.select_one("body")
    if body is not None:
        m = _POSTID_CLASS_RE.search(" ".join(body.get("class") or []))
        if m:
            return m.group(1)
    if raw_html is not None:
        m = _POST_ID_ATTR_RE.search(raw_html)
        return m.group(1) if m else ""
    m = _POST_ID_ATTR_RE.search(str(soup))
    if m:
        return m.group(1)
    return ""


def _extract_artists(soup: BeautifulSoup, idx: dict[str, list[str]] | None = None) -> list[str]:
    idx = idx if idx is not None else meta_index(soup)
    author = meta_get(idx, "author")
    if author:
        return [author]
    title_tag = soup.select_one("title")
    page_title = unescape(title_tag.get_text(strip=True)) if title_tag else ""
    if not page_title:
        return []

    parts = [p.strip() for p in page_title.split(" - ") if p.strip()]
    site_name = meta_get(idx, "og:site_name")
    if site_name and parts and parts[-1] == site_name:
        parts = parts[:-1]
    else:
        domain_lower = DOMAIN.replace(".com", "").lower()
        if parts and parts[-1].lower().startswith(domain_lower):
            parts = parts[:-1]
    if len(parts) >= 2:
        return [parts[1]]
    return []


def _extract_genres(soup: BeautifulSoup, idx: dict[str, list[str]] | None = None) -> list[str]:
    idx = idx if idx is not None else meta_index(soup)
    genres: list[str] = []
    genres.extend(idx.get("prop:article:tag", []))
    genres.extend(idx.get("name:article:tag", []))
    return list(dict.fromkeys(genres))


def _extract_publisher(soup: BeautifulSoup, idx: dict[str, list[str]] | None = None) -> str:
    """Publisher from the OpenGraph ``article:section`` tag, with WordPress
    JSON-LD (Article/NewsArticle .publisher.name) as fallback.

    FSI sets ``article:section`` to the studio (e.g. "Super Melons"), which is
    the meaningful publisher for readers; the JSON-LD ``publisher`` is usually
    just the site name ("FSI Comics").
    """
    if idx:
        for v in idx.get("prop:article:section", []):
            if v and v.strip():
                return v.strip()
    for s in soup.select('script[type="application/ld+json"]'):
        if not s.string:
            continue
        try:
            data = json.loads(unescape(s.string))
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            node = item
            if node.get("@graph") and isinstance(node["@graph"], list):
                for sub in node["@graph"]:
                    if isinstance(sub, dict) and sub.get("@type") in (
                        "Article", "NewsArticle",
                    ):
                        node = sub
                        break
            if node.get("@type") not in ("Article", "NewsArticle"):
                continue
            pub = node.get("publisher")
            if isinstance(pub, dict):
                name = pub.get("name", "")
                if isinstance(name, str) and name.strip():
                    return name.strip()
    return ""


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class FsicomixScraper(BaseScraper):
    """FSIComics chapter and series scraper."""

    domain = DOMAIN
    name = "fsicomics"

    def matches_url(self, url: str) -> bool:
        return is_chapter_url(url) or is_series_url(url)

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        return await self._scrape_series(url, client)

    async def _scrape_chapter(
        self, url: str, client: AsyncSession,
    ) -> ScrapedChapter:
        soup, html = await self.fetch_html_raw(url, client)
        idx = meta_index(soup)

        if _is_archive_page(soup):
            raise listing_page_error(
                "FSI Comics", f"{BASE}/{{comic-slug}}/",
            )

        series_title, chapter_title = _extract_meta(soup, idx)
        derived_series = _derive_series_title(url, chapter_title)
        if derived_series:
            series_title = derived_series
        description = _extract_description(soup, idx)
        cover_url = _extract_cover(soup, idx)
        images = _extract_images(soup)
        artists = _extract_artists(soup, idx)
        genres = _extract_genres(soup, idx)
        publisher = _extract_publisher(soup, idx)

        if not chapter_title:
            title_tag = soup.select_one("title")
            chapter_title = unescape(title_tag.get_text(strip=True)) if title_tag else ""

        chapter_number = _extract_chapter_number(chapter_title)
        if not chapter_number:
            page_title = soup.select_one("title")
            if page_title:
                chapter_number = _extract_chapter_number(unescape(page_title.get_text(strip=True)))

        lang = ""
        html_tag = soup.select_one("html")
        if html_tag and html_tag.get("lang"):
            lang = _attr_text(html_tag.get("lang")).split("-")[0].lower()

        if not images:
            raise no_images_error()

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=sanitize_filename(series_title) or "Untitled",
                chapter_title=sanitize_filename(chapter_title) or "Chapter",
                chapter_number=chapter_number,
                description=description,
                language=lang,
                artists=artists,
                genres=genres,
                publisher=publisher,
                reading_direction="ltr",
                total_pages=len(images),
            ),
            source=SourceInfo(url=url, service=DOMAIN, post_id=_extract_post_id(soup, html)),
            images=images,
            cover_url=cover_url,
        )

    async def _scrape_series(
        self, url: str, client: AsyncSession,
    ) -> SeriesMetadata:
        soup = await self.fetch_html(url, client)

        if _is_archive_page(soup):
            raise listing_page_error(
                "FSI Comics", f"{BASE}/{{comic-slug}}/",
            )

        idx = meta_index(soup)

        title_tag = soup.select_one("title")
        page_title = title_tag.get_text(strip=True) if title_tag else ""
        parts = [p.strip() for p in page_title.split(" - ") if p.strip()]
        series_title = parts[0] if parts else ""

        description = _extract_description(soup, idx)
        cover_url = _extract_cover(soup, idx)

        title_no = url.rstrip("/").rsplit("/", 1)[-1]

        chapters: list[dict] = []
        seen_urls: set[str] = set()

        pages_to_fetch: list[tuple[str, BeautifulSoup | None]] = [(url, soup)]
        sem = asyncio.Semaphore(3)

        async def fetch_page(u: str) -> tuple[str, BeautifulSoup | None]:
            async with sem:
                try:
                    pr = await BaseScraper._timeout_get(u, client)
                    pr.raise_for_status()
                    return u, BeautifulSoup(pr.text, "lxml")
                except Exception:
                    return u, None

        page_num = 2
        has_next = soup.select_one("a.next.page-numbers") is not None
        while has_next and page_num <= 50:
            page_url = f"{url.rstrip('/')}/page/{page_num}/"
            u, ps = await fetch_page(page_url)
            pages_to_fetch.append((u, ps))
            if ps is None:
                break
            has_next = ps.select_one("a.next.page-numbers") is not None
            page_num += 1

        for _page_url, ps in pages_to_fetch:
            if ps is None:
                continue
            for article in ps.select("article"):
                link = article.select_one("h2.entry-title a[href]")
                if not link:
                    continue
                href = _attr_text(link.get("href"))
                if not href or href in seen_urls:
                    continue
                seen_urls.add(href)
                ch_title = link.get_text(strip=True) or ""
                chapters.append({
                    "title": ch_title,
                    "url": urljoin(url, href),
                    "episode_no": str(len(chapters) + 1),
                })

        if not chapters:
            raise no_chapters_error()

        return SeriesMetadata(
            series_title=series_title or title_no.replace("-", " ").title(),
            description=description,
            cover_url=cover_url,
            title_no=title_no,
            chapters=chapters,
        )
