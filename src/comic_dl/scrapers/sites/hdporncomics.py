"""HD Porn Comics (hdporncomics.com) gallery scraper.

WordPress site whose Cloudflare binds clearance to the minting TLS
fingerprint: a stored cookie never replays over plain HTTP, so every
page fetch runs as a same-origin XHR inside the webview request session
(the kagane pattern). Without a runnable webview the site is unreachable
and scrape fails with a solver hint instead of a bare 403.

Posts are standalone galleries (no series): the page images are the
``/thumbs/<hash>/`` group with the most members, excluding thumbnail
boxes (alt text containing "thumbnail"); related-post widgets reuse the
same CDN with one image per hash.
"""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from ... import webview
from ...antibot import looks_like_challenge
from ...cf import note_replay_dead, replay_dead
from ...errors import ScrapeError
from ...http import jar_cookies_for
from ...models import (
    ChapterInfo,
    ImageItem,
    PostMetadata,
    ScrapedChapter,
    SourceInfo,
    chapter_to_post_metadata,
)
from ...rate import await_ratelimit
from ...utils import validate_request_url_async
from ..base import (
    BaseScraper,
    _attr_text,
    listing_page_error,
    meta_get,
    meta_index,
    no_images_error,
)
from ..registry import register_scraper

DOMAIN = "hdporncomics.com"
BASE = "https://hdporncomics.com"

_POST_PATH_RE = re.compile(r"^https?://(?:www\.)?hdporncomics\.com/[a-z0-9-]+-sex-comic/?$")
_THUMB_HASH_RE = re.compile(r"/thumbs/([a-f0-9]+)/")
_POST_ID_RE = re.compile(r"\bpostid-(\d+)\b")
_TITLE_SUFFIX_RE = re.compile(r"\s+comic porn\s*$", re.IGNORECASE)
_PAGE_NUMBER_RE = re.compile(r"(\d+)\.[a-z0-9]+$", re.IGNORECASE)


def is_gallery_url(url: str) -> bool:
    """True when ``url`` points at an HD Porn Comics gallery post."""
    return bool(_POST_PATH_RE.match(url))


def _has_clearance(url: str) -> bool:
    """True when the jar holds a ``cf_clearance`` for ``url``'s host."""
    return "cf_clearance" in jar_cookies_for(url)


def _strip_title_suffix(title: str) -> str:
    return _TITLE_SUFFIX_RE.sub("", title.strip()).strip()


def _extract_post_id(soup: BeautifulSoup) -> str:
    body = soup.select_one("body")
    classes: object = body.get("class") if body is not None else None
    if isinstance(classes, str):
        classes = [classes]
    if not isinstance(classes, list):
        return ""
    m = _POST_ID_RE.search(" ".join(str(c) for c in classes))
    return m.group(1) if m else ""


def _extract_tags(soup: BeautifulSoup) -> list[str]:
    return [
        a.get_text(strip=True) for a in soup.select('a[href*="/tag/"]') if a.get_text(strip=True)
    ]


def _extract_artists(soup: BeautifulSoup) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for a in soup.select('a[href*="/artist/"]'):
        name = a.get_text(strip=True)
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def _extract_images(soup: BeautifulSoup, base: str) -> list[ImageItem]:
    """Page images: the largest ``/thumbs/<hash>/`` group, minus thumbnails."""
    groups: dict[str, list[str]] = {}
    for img in soup.select('img[src*="/thumbs/"]'):
        alt = _attr_text(img.get("alt"))
        if "thumbnail" in alt.lower():
            continue
        src = _attr_text(img.get("src"))
        m = _THUMB_HASH_RE.search(src)
        if not m or not src or src.startswith("data:"):
            continue
        groups.setdefault(m.group(1), []).append(src)
    if not groups:
        return []
    counts = Counter({h: len(set(urls)) for h, urls in groups.items()})
    best, _ = counts.most_common(1)[0]
    items: list[ImageItem] = []
    seen: set[str] = set()
    for src in groups[best]:
        url = urljoin(base, src)
        if url in seen:
            continue
        seen.add(url)
        m = _PAGE_NUMBER_RE.search(url)
        page_number = int(m.group(1)) if m else len(items) + 1
        item = ImageItem.from_url(url, page_number)
        if item is not None:
            items.append(item)
    return sorted(items, key=lambda i: i.page_number)


@register_scraper(domain=DOMAIN, capabilities={"chapter"})
class HDPornComicsScraper(BaseScraper):
    """HD Porn Comics gallery scraper (webview-session transport)."""

    domain = DOMAIN
    name = "hdporncomics"
    site_id = "hdporncomics"
    version = "1.0.0"
    minimum_core_version = "0.0.2"

    def matches_url(self, url: str) -> bool:
        return is_gallery_url(url)

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def _fetch_html(self, url: str, client: AsyncSession) -> BeautifulSoup:
        """Page HTML, preferring a stored-cookie replay over the session.

        Plain HTTP is tried first while the jar holds clearance and this
        run has not seen a challenge; otherwise the webview session serves
        the request. Raises :class:`ScrapeError` with a solver hint when
        neither transport can run here.
        """
        from urllib.parse import urlsplit

        host = (urlsplit(url).hostname or "").lower()
        if not replay_dead(host) and _has_clearance(url):
            resp = await BaseScraper._timeout_get(
                url, client, challenge_retry=False, use_cache=False
            )
            if not looks_like_challenge(
                resp.status_code,
                getattr(resp, "headers", None),
                getattr(resp, "text", "") or "",
            ):
                return BeautifulSoup(resp.text, "lxml")
            note_replay_dead(host)
        if webview.session_enabled():
            try:
                session = await webview.ensure_session(BASE)
            except Exception:
                session = None
            if session is not None:
                await await_ratelimit(host or DOMAIN)
                current = await validate_request_url_async(url)
                status, _headers, content = await session.request("GET", current)
                if status == 200 and content:
                    return BeautifulSoup(content.decode("utf-8", errors="replace"), "lxml")
        resp = await BaseScraper._timeout_get(url, client, use_cache=False)
        if looks_like_challenge(
            resp.status_code,
            getattr(resp, "headers", None),
            getattr(resp, "text", "") or "",
        ):
            raise ScrapeError(
                "Cloudflare challenged the hdporncomics.com request.",
                hint=(
                    "This site only answers the webview solver — run with a "
                    "display (default --solver auto) and pass the challenge "
                    "in the browser window."
                ),
            )
        return BeautifulSoup(resp.text, "lxml")

    async def _scrape_chapter(self, url: str, client: AsyncSession) -> ScrapedChapter:
        if not is_gallery_url(url):
            raise listing_page_error("HD Porn Comics", f"{BASE}/{{slug}}-sex-comic/")
        soup = await self._fetch_html(url, client)
        idx = meta_index(soup)

        h1 = soup.select_one("h1")
        title = _strip_title_suffix(h1.get_text(strip=True)) if h1 else ""
        if not title:
            title = _strip_title_suffix(meta_get(idx, "og:title").split("|")[0])
        title = title or "Untitled"

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
                genres=_extract_tags(soup),
                artists=_extract_artists(soup),
                language=lang,
                reading_direction="ltr",
                total_pages=len(images),
            ),
            source=SourceInfo(url=url, service=DOMAIN, post_id=_extract_post_id(soup) or url),
            images=images,
            cover_url=images[0].url,
        )
