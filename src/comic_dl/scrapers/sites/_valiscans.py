"""Shared scraper base for the ValirScans/DivaScans platform (Next.js + API).

Series pages are server-rendered Next.js flight data: the chapter roster
lives in an escaped ``"chapters":[...]`` RSC array (id, number, title,
``isLocked``/``coinPrice``/``hasAccess``). Chapter images are NOT in the
HTML — ``GET /api/chapters/page-urls?chapterId=<cuid>`` returns page
objects with direct ``imageUrl``s. Free chapters serve real images
anonymously; locked chapters come back redacted (401 for ``content``),
so listings skip them and single locked URLs fail with a coin hint.

This module is underscore-prefixed so auto-discovery skips it; concrete
per-domain subclasses live in their own modules.
"""

from __future__ import annotations

import html
import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from ...errors import ScrapeError
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
    listing_page_error,
    meta_get,
    meta_index,
    no_chapters_error,
    no_images_error,
)

_CHAPTER_NUMBER_RE = re.compile(r"/chapter/(\d+)/?$")

# The API serves the SPA shell (200 HTML) to document-navigation requests
# and JSON only to XHR-style ones; curl_cffi impersonation sends the
# former by default, so API calls carry explicit fetch headers.
_API_HEADERS = {
    "Accept": "*/*",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}
_SERIES_STATUS_RE = re.compile(r'\\"status\\":\\"([A-Z_]+)\\"')


def series_url_re(domain: str) -> re.Pattern:
    """Comic series-page pattern for a ValiScans-platform domain."""
    return re.compile(rf"^https?://(?:www\.)?{re.escape(domain)}/series/comic/[a-z0-9-]+/?$")


def chapter_url_re(domain: str) -> re.Pattern:
    """Comic chapter-page pattern for a ValiScans-platform domain."""
    return re.compile(
        rf"^https?://(?:www\.)?{re.escape(domain)}/series/comic/[a-z0-9-]+/chapter/\d+/?$"
    )


def _series_slug_from_url(url: str) -> str:
    parts = [p for p in urlparse(url).path.rstrip("/").split("/") if p]
    try:
        i = parts.index("comic")
    except ValueError:
        return ""
    return parts[i + 1] if i + 1 < len(parts) else ""


def _chapter_number_from_url(url: str) -> str | None:
    m = _CHAPTER_NUMBER_RE.search(url.rstrip("/"))
    return m.group(1) if m else None


def extract_rsc_string(raw: str, key: str) -> str:
    """Value of an escaped ``"key":"..."`` string in flight data.

    Ends at the first ``\\"``: prose values carry special characters as
    ``\\uXXXX`` escapes, never as embedded ``\\"`` pairs. The value is
    JS-unescaped but HTML entities are left for the caller
    (``html.unescape``) so text and markup stay apart. Returns ``""``
    when the key is absent.
    """
    marker = f'\\"{key}\\":\\"'
    i = raw.find(marker)
    if i == -1:
        return ""
    j = raw.find('\\"', i + len(marker))
    if j == -1:
        return ""
    try:
        return json.loads('"' + raw[i + len(marker) : j] + '"')
    except json.JSONDecodeError:
        return ""


def extract_rsc_array(raw: str, key: str) -> list:
    """Parse an escaped ``"key":[...]`` array out of Next.js flight data.

    The payload is a JS string layer, so brackets inside ``\\"...\\"``
    pairs are content, not structure: backslash pairs are skipped whole
    and only bare brackets count. The decoded text is normalized
    (``$undefined``/``!0``/``!1`` are RSC spellings) before ``json.loads``.
    Returns ``[]`` when the key is absent or unparseable.
    """
    prefix = f'\\"{key}\\":['
    start = raw.find(prefix)
    if start == -1:
        return []
    j = start + len(prefix) - 1  # at '['
    n = len(raw)
    depth, in_str = 0, False
    while j < n:
        ch = raw[j]
        if ch == "\\" and j + 1 < n:
            j += 2
            continue
        if ch == '"':
            in_str = not in_str
        elif not in_str and ch == "[":
            depth += 1
        elif not in_str and ch == "]":
            depth -= 1
            if depth == 0:
                break
        j += 1
    try:
        text = json.loads('"' + raw[start + len(prefix) - 1 : j + 1] + '"')
    except json.JSONDecodeError:
        return []
    text = re.sub(r"\$undefined", "null", text)
    text = re.sub(r"(?<![\w$])!1(?![\w$])", "true", text)
    text = re.sub(r"(?<![\w$])!0(?![\w$])", "false", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _is_free(entry: dict) -> bool:
    """Free chapters need no coins; locked ones come back redacted."""
    return not entry.get("isLocked") and bool(entry.get("hasAccess"))


def _extract_description(raw: str, soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    """Full synopsis from the flight-data ``description`` HTML.

    Falls back to ``og:description``, which the site truncates.
    """
    text = extract_rsc_string(raw, "description")
    if text:
        plain = BeautifulSoup(text, "lxml").get_text("\n", strip=True)
        cleaned = html.unescape(plain).strip()
        if cleaned:
            return cleaned
    return meta_get(idx, "og:description", "twitter:description")


def _extract_cover(base: str, raw: str, soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    """Series cover from the flight-data ``coverImage`` path.

    ``/uploads/api/<hash>.jpg`` also exists on the media CDN (smaller,
    canonical); anything else resolves against the site itself.
    """
    text = extract_rsc_string(raw, "coverImage")
    if text:
        name = text.strip().rsplit("/", 1)[-1]
        host = urlparse(base).hostname or ""
        if text.startswith("/uploads/api/") and host:
            return f"https://media.{host}/api/{name}"
        if text.startswith("/"):
            return urljoin(base, text)
        return text
    return meta_get(idx, "og:image", "twitter:image")


def _extract_title(soup: BeautifulSoup, idx: dict[str, list[str]]) -> str:
    og = meta_get(idx, "og:title")
    if og:
        return og.strip()
    title_tag = soup.select_one("title")
    text = title_tag.get_text(strip=True) if title_tag else ""
    text = re.split(r"\s+by\s+|\s*\|\s*", text)[0].strip()
    return text


def _extract_genres(raw: str) -> list[str]:
    names: list[str] = []
    for entry in extract_rsc_array(raw, "genres"):
        if isinstance(entry, dict) and entry.get("name"):
            names.append(str(entry["name"]))
    return names


def _extract_status(raw: str) -> str | None:
    m = _SERIES_STATUS_RE.search(raw)
    return m.group(1).capitalize() if m else None


class ValiScansScraper(BaseScraper):
    """ValiScans-platform chapter + series scraper, parameterized by domain.

    Subclasses set ``domain``/``name``/``site_id`` and register themselves;
    all behavior lives here.
    """

    domain: str = ""

    def matches_url(self, url: str) -> bool:
        return self._is_chapter(url) or self._is_series(url)

    def matches_series_url(self, url: str) -> bool:
        return self._is_series(url)

    def _is_chapter(self, url: str) -> bool:
        return bool(chapter_url_re(self.domain).match(url))

    def _is_series(self, url: str) -> bool:
        return bool(series_url_re(self.domain).match(url)) and not self._is_chapter(url)

    def _is_novel(self, url: str) -> bool:
        """Novel URLs share the series shape; comics only past this point."""
        return "/series/novel/" in urlparse(url).path

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        return await self._scrape_series(url, client)

    def _base(self) -> str:
        return f"https://{self.domain}"

    async def _series_roster(self, slug: str, client: AsyncSession) -> tuple[dict, list[dict]]:
        """Series metadata + free chapter entries (number-keyed source of truth)."""
        soup, raw = await self.fetch_html_raw(f"{self._base()}/series/comic/{slug}", client)
        idx = meta_index(soup)
        meta = {
            "title": _extract_title(soup, idx),
            "description": _extract_description(raw, soup, idx),
            "cover_url": _extract_cover(self._base(), raw, soup, idx),
            "genres": _extract_genres(raw),
            "status": _extract_status(raw),
        }
        entries = [
            e for e in extract_rsc_array(raw, "chapters") if isinstance(e, dict) and _is_free(e)
        ]
        return meta, entries

    async def _chapter_images(self, chapter_id: str, client: AsyncSession) -> list[ImageItem]:
        resp = await BaseScraper._timeout_get(
            f"{self._base()}/api/chapters/page-urls?chapterId={chapter_id}",
            client,
            headers=dict(_API_HEADERS),
            expect_json=True,
        )
        resp.raise_for_status()
        try:
            body = resp.json()
        except ValueError:
            return []
        pages = body.get("pages", []) if isinstance(body, dict) else []
        images: list[ImageItem] = []
        for page in pages if isinstance(pages, list) else []:
            if not isinstance(page, dict):
                continue
            src = page.get("imageUrl") or ""
            if not src or page.get("isRedacted") or page.get("isEncrypted"):
                continue
            n = page.get("pageNumber", len(images) + 1)
            page_number = n if isinstance(n, int) and not isinstance(n, bool) else len(images) + 1
            item = ImageItem.from_url(urljoin(self._base(), str(src)), page_number)
            if item is not None:
                images.append(item)
        return sorted(images, key=lambda i: i.page_number)

    async def _scrape_chapter(self, url: str, client: AsyncSession) -> ScrapedChapter:
        if self._is_novel(url):
            raise ScrapeError(
                "Novel chapters have no images to download.",
                hint="Only comic series are supported on this site.",
            )
        if not self._is_chapter(url):
            raise listing_page_error(self.domain, url)
        slug = _series_slug_from_url(url)
        number = _chapter_number_from_url(url)
        if not slug or not number:
            raise no_images_error()
        meta, entries = await self._series_roster(slug, client)
        match = next(
            (e for e in entries if str(e.get("number")) == number),
            None,
        )
        if match is None:
            raise no_images_error(
                hint="This chapter may require coins — only free chapters are supported.",
            )
        images = await self._chapter_images(str(match["id"]), client)
        if not images:
            raise no_images_error()
        title = str(match.get("title") or "").strip() or f"Chapter {number}"
        return ScrapedChapter(
            info=ChapterInfo(
                series_title=meta["title"] or slug.replace("-", " ").title(),
                chapter_title=title,
                chapter_number=number,
                description=meta["description"],
                genres=meta["genres"],
                status=meta["status"],
                language="en",
                reading_direction="ltr",
                total_pages=len(images),
            ),
            source=SourceInfo(url=url, service=self.domain, post_id=str(match["id"])),
            images=images,
            cover_url=meta["cover_url"],
        )

    async def _scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        if self._is_novel(url):
            raise ScrapeError(
                "Novel series have no images to download.",
                hint="Only comic series are supported on this site.",
            )
        if not self._is_series(url):
            raise listing_page_error(self.domain, url)
        slug = _series_slug_from_url(url)
        if not slug:
            raise listing_page_error(self.domain, url)
        meta, entries = await self._series_roster(slug, client)
        chapters = [
            {
                "title": str(e.get("title") or "").strip() or f"Chapter {e.get('number')}",
                "url": f"{self._base()}/series/comic/{slug}/chapter/{e.get('number')}",
                "episode_no": str(e.get("number")),
            }
            for e in sorted(entries, key=lambda e: float(e.get("number", 0)))
        ]
        if not chapters:
            raise no_chapters_error()
        return SeriesMetadata(
            series_title=meta["title"] or slug.replace("-", " ").title(),
            description=meta["description"],
            cover_url=meta["cover_url"],
            title_no=slug,
            chapters=chapters,
        )
