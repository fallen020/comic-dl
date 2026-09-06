"""FlameComics scraper."""

from __future__ import annotations

import json
import re

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
from ...ui import DIAGNOSTIC, TAG_SCRAPE, vlog
from ..base import (
    BaseScraper,
    _attr_text,
    meta_get,
    meta_index,
    no_chapters_error,
    no_images_error,
)
from ..registry import register_scraper

DOMAIN = "flamecomics.xyz"
BASE = "https://flamecomics.xyz"
CDN = "https://cdn.flamecomics.xyz"

SERIES_PATTERN = re.compile(
    r"^https?://(?:www\.)?flamecomics\.xyz/series/(\d+)/?$"
)

CHAPTER_PATTERN = re.compile(
    r"^https?://(?:www\.)?flamecomics\.xyz/series/(\d+)/([a-f0-9]+)/?$"
)

_NEXT_DATA_SEL = 'script#__NEXT_DATA__[type="application/json"]'
_JSONLD_SEL = 'script[type="application/ld+json"]'
_ASSETS_PREFIX = "/assets/read/"

_VALID_EXTS = frozenset({"jpg", "jpeg", "png", "webp", "gif", "bmp"})


def _canonical_chapter_number(raw: str) -> str:
    """Canonicalize a numeric chapter label without mangling trailing zeros.

    ``"10.0"`` → ``"10"``, ``"100"`` → ``"100"``, ``"1.5"`` → ``"1.5"``.
    Non-numeric labels are returned unchanged. The old ``rstrip("0")``
    approach turned chapter 100 into chapter 1.
    """
    value = raw.strip()
    try:
        as_float = float(value)
    except ValueError:
        return value
    if as_float.is_integer():
        return str(int(as_float))
    text = repr(as_float)
    if "." not in text:
        return text
    return text.rstrip("0").rstrip(".")


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a series page for this source."""
    return bool(SERIES_PATTERN.match(url))


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a chapter/gallery page for this source."""
    return bool(CHAPTER_PATTERN.match(url))


@register_scraper(domain=DOMAIN, capabilities={"chapter", "series"})
class FlameScraper(BaseScraper):
    """FlameComics chapter and series scraper (Next.js JSON-LD site)."""

    domain = DOMAIN
    name = "flamecomics"

    def matches_url(self, url: str) -> bool:
        return is_chapter_url(url) or is_series_url(url)

    def __init__(self) -> None:
        super().__init__()
        self._series_cache: dict[str, dict] = {}

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        return await self._scrape_series(url, client)

    async def _series_page_data(self, series_id: str, client: AsyncSession) -> dict:
        """Fetch the series page once per series_id (cached on the instance)."""
        cached = self._series_cache.get(series_id)
        if cached is not None:
            return cached
        data: dict = {}
        try:
            response = await BaseScraper._timeout_get(
                f"{BASE}/series/{series_id}/", client
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            nd = self._find_next_data(soup)
            if nd:
                data = (
                    nd.get("props", {})
                    .get("pageProps", {})
                    .get("series", {})
                )
        # Enrichment is best-effort.
        except Exception as exc:  # nosec B110
            vlog(
                DIAGNOSTIC,
                f"series enrichment unavailable for {series_id}: "
                f"{type(exc).__name__}",
                tag=TAG_SCRAPE,
            )
        self._series_cache[series_id] = data
        return data

    async def _scrape_chapter(
        self, url: str, client: AsyncSession,
    ) -> ScrapedChapter:
        soup = await self.fetch_html(url, client)
        idx = meta_index(soup)

        series_title = ""
        chapter_title = ""
        chapter_data: dict = {}
        authors: list[str] = []
        artists: list[str] = []
        genres: list[str] = []
        language: str | None = None
        reading_direction: str | None = None
        chapter_number: str | None = None
        publisher: str | None = None
        status: str | None = None
        year: int | None = None
        series_id: str | None = None
        description = meta_get(idx, "og:description", "description", "twitter:description")
        cover_url = meta_get(idx, "og:image", "twitter:image")
        site_name = meta_get(idx, "og:site_name")

        # Priority 1: __NEXT_DATA__ — present on most chapter pages
        data = self._find_next_data(soup)
        if data:
            page_props = data.get("props", {}).get("pageProps", {})
            chapter_data = page_props.get("chapter") or {}
            series_data = page_props.get("series") or {}

            series_title = series_data.get("title", "") or chapter_data.get("series_title", "")
            # NOTE: chapter["title"] nests the SERIES title on this site (its
            # chapter_title field is often empty), so it must never be read as
            # the chapter label — that would shadow the JSON-LD fallback below.
            chapter_title = (chapter_data.get("chapter_title") or "").strip()

            raw_authors = series_data.get("author") or chapter_data.get("author")
            if isinstance(raw_authors, list):
                authors = raw_authors
            elif isinstance(raw_authors, str):
                authors = [raw_authors]

            raw_artists = series_data.get("artist") or chapter_data.get("artist")
            if isinstance(raw_artists, list):
                artists = raw_artists
            elif isinstance(raw_artists, str):
                artists = [raw_artists]

            raw_genres = series_data.get("genres") or chapter_data.get("genres")
            if isinstance(raw_genres, list):
                genres = raw_genres

            raw_lang = series_data.get("language") or chapter_data.get("language")
            if raw_lang:
                language = str(raw_lang)

            raw_ch = chapter_data.get("chapter")
            if raw_ch is not None:
                chapter_number = _canonical_chapter_number(str(raw_ch))

            sid = series_data.get("series_id") or chapter_data.get("series_id")
            if sid is not None:
                series_id = str(sid)

            raw_type = series_data.get("type") or chapter_data.get("type")
            if raw_type:
                # Manga reads right-to-left; manhwa/manhua/webtoon left-to-right.
                reading_direction = (
                    "rtl" if str(raw_type).strip().lower() == "manga" else "ltr"
                )

            # The site's own CDN filename is authoritative; og:image can
            # carry a stale social-preview thumbnail, so it stays fallback.
            cover = series_data.get("cover", "") or chapter_data.get("cover", "")
            sid_cover = series_data.get("series_id") or chapter_data.get(
                "series_id"
            )
            if cover and sid_cover:
                cover_url = f"{CDN}/uploads/images/series/{sid_cover}/{cover}"

            if not genres:
                raw_tags = chapter_data.get("tags")
                if isinstance(raw_tags, list):
                    genres = [t for t in raw_tags if isinstance(t, str) and t]

            if not description:
                raw_desc = chapter_data.get("description")
                if isinstance(raw_desc, str) and raw_desc:
                    desc_soup = BeautifulSoup(raw_desc, "lxml")
                    description = desc_soup.get_text(strip=True)

        # Priority 2: JSON-LD with @type Chapter (series page data)
        if not series_title or not chapter_title:
            ld = self._find_jsonld(soup)
            if ld and ld.get("@type") == "Chapter":
                series_title = series_title or (ld.get("isPartOf") or {}).get("name", "")
                name = ld.get("name", "")
                if not chapter_title:
                    if series_title and name.startswith(series_title):
                        chapter_title = name[len(series_title):].lstrip(" -")
                    else:
                        chapter_title = name

                if not authors:
                    raw = ld.get("author")
                    if isinstance(raw, dict):
                        authors = [raw.get("name", "")]
                    elif isinstance(raw, list):
                        authors = [
                            a.get("name", "") if isinstance(a, dict) else str(a)
                            for a in raw
                        ]

                if not artists:
                    raw = ld.get("illustrator")
                    if isinstance(raw, dict):
                        artists = [raw.get("name", "")]
                    elif isinstance(raw, list):
                        artists = [
                            a.get("name", "") if isinstance(a, dict) else str(a)
                            for a in raw
                        ]

                if not genres:
                    raw = ld.get("genre")
                    if isinstance(raw, list):
                        genres = [g for g in raw if isinstance(g, str)]

                if not language:
                    raw = ld.get("inLanguage")
                    if isinstance(raw, str):
                        language = raw

                if year is None and not publisher and not status:
                    raw = ld.get("datePublished")
                    if isinstance(raw, str):
                        m = re.match(r"^(\d{4})", raw)
                        if m:
                            year = int(m.group(1))

        # Priority 3: meta tags for titles
        if not series_title:
            series_title = meta_get(idx, "og:title", "twitter:title")
            if series_title:
                series_title = series_title.split(" - ")[0].strip()
        if not series_title:
            series_title = site_name

        if not chapter_title:
            title_tag = soup.select_one("title")
            if title_tag and title_tag.string:
                text = title_tag.string.strip()
                parts = [p.strip() for p in text.split(" - ") if p.strip()]
                if site_name and len(parts) >= 2 and parts[-1] == site_name:
                    chapter_title = parts[0]
                    if not series_title and len(parts) >= 3:
                        series_title = parts[-2]
                elif len(parts) >= 2:
                    chapter_title = parts[0]
                    if not series_title:
                        series_title = parts[-1]

        if not chapter_title and not series_title:
            title_tag = soup.select_one("title")
            if title_tag and title_tag.string:
                text = title_tag.string.strip()
                series_title = text.split(" - ")[0].strip()

        # Final title guards: a label that merely repeats the series title is
        # useless (it nests inside chapter["title"] on this site), and the
        # archive naming/ComicInfo depend on a non-empty chapter label.
        if (
            chapter_title
            and series_title
            and chapter_title.strip().casefold() == series_title.strip().casefold()
        ):
            chapter_title = ""
        if not chapter_title:
            if chapter_number:
                chapter_title = f"Ch. {chapter_number}"
            else:
                token = chapter_data.get("token") or ""
                if not token:
                    m = CHAPTER_PATTERN.match(url)
                    token = m.group(2) if m else ""
                chapter_title = token

        images = self._images_from_page(soup)
        if not images:
            raise no_images_error()

        # Enrichment: the chapter page lacks publisher/status/type/year; the
        # series page carries them. Best-effort and cached per series_id.
        if (series_id and not year and not publisher and not status) or (
            series_id and reading_direction is None
        ):
            series = await self._series_page_data(series_id, client)
            if series:
                if not publisher:
                    raw_pub = series.get("publisher")
                    if isinstance(raw_pub, list):
                        publisher = ", ".join(str(p) for p in raw_pub if str(p))
                    elif isinstance(raw_pub, str) and raw_pub:
                        publisher = raw_pub
                if not status:
                    raw_status = series.get("status")
                    if isinstance(raw_status, str) and raw_status:
                        status = raw_status
                if year is None:
                    raw_year = series.get("year")
                    if isinstance(raw_year, int):
                        year = raw_year
                    elif isinstance(raw_year, str) and raw_year.isdigit():
                        year = int(raw_year)
                if reading_direction is None:
                    raw_type = series.get("type")
                    if isinstance(raw_type, str) and raw_type:
                        # Manga reads right-to-left; manhwa/manhua/webtoon left-to-right.
                        reading_direction = (
                            "rtl" if raw_type.strip().lower() == "manga" else "ltr"
                        )

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=series_title,
                chapter_title=chapter_title,
                chapter_number=chapter_number,
                description=description,
                authors=authors,
                artists=artists,
                genres=genres,
                publisher=publisher,
                status=status,
                language=language,
                reading_direction=reading_direction,
                year=year,
                total_pages=len(images),
            ),
            source=SourceInfo(url=url, service=DOMAIN),
            images=images,
            cover_url=cover_url,
        )

    async def _scrape_series(
        self, url: str, client: AsyncSession,
    ) -> SeriesMetadata:
        soup = await self.fetch_html(url, client)
        idx = meta_index(soup)

        data = self._find_next_data(soup)
        if not data:
            raise ScrapeError(
                "Could not find series data on page.",
                hint="The FlameComics page layout may have changed.",
            )

        page_props = data.get("props", {}).get("pageProps", {})
        series_data = page_props.get("series", {})
        chapters_data = page_props.get("chapters", [])

        series_id = str(series_data.get("series_id", ""))
        series_title = series_data.get("title", "")
        description = series_data.get("description", "")
        if description:
            desc_soup = BeautifulSoup(description, "lxml")
            description = desc_soup.get_text(strip=True)

        cover = series_data.get("cover", "")
        if cover:
            cover_url = f"{CDN}/uploads/images/series/{series_id}/{cover}"
        else:
            cover_url = meta_get(idx, "og:image", "twitter:image")

        chapters: list[dict] = []
        seen: set[str] = set()
        for ch in chapters_data:
            chapter_str = str(ch.get("chapter", ""))
            if chapter_str in seen:
                continue
            seen.add(chapter_str)
            token = ch.get("token", "")
            ch_title = ch.get("title") or ""
            episode_no = _canonical_chapter_number(chapter_str)
            title = (
                f"Ch. {episode_no} - {ch_title}"
                if ch_title
                else f"Ch. {episode_no}"
            )
            ch_url = f"{BASE}/series/{series_id}/{token}"
            chapters.append({
                "title": title,
                "url": ch_url,
                "episode_no": episode_no or title,
            })

        if not chapters:
            raise no_chapters_error()

        chapters.reverse()

        return SeriesMetadata(
            series_title=series_title,
            description=description,
            cover_url=cover_url,
            title_no=series_id,
            chapters=chapters,
        )

    @staticmethod
    def _find_next_data(soup: BeautifulSoup) -> dict | None:
        script = soup.select_one(_NEXT_DATA_SEL)
        if script and script.string:
            try:
                return json.loads(script.string)
            except json.JSONDecodeError:
                pass
        return None

    @staticmethod
    def _find_jsonld(soup: BeautifulSoup) -> dict | None:
        scripts = soup.select(_JSONLD_SEL)
        for s in scripts:
            if not s.string:
                continue
            try:
                data = json.loads(s.string)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                # Chapter pages carry Organization / WebSite / Chapter /
                # BreadcrumbList blocks; only the Chapter node holds the
                # chapter fields we read below.
                if data.get("@type") == "Chapter":
                    return data
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") == "Chapter":
                        return item
        return None

    @staticmethod
    def _images_from_page(soup: BeautifulSoup) -> list[ImageItem]:
        images: list[ImageItem] = []
        seen: set[str] = set()

        for img in soup.select("img[src]"):
            src = _attr_text(img.get("src"))
            if not src:
                continue

            if _ASSETS_PREFIX in src:
                continue

            if src.startswith("/_next/"):
                continue

            clean_url = src.split("?")[0]
            if clean_url in seen:
                continue
            seen.add(clean_url)

            images.append(ImageItem(url=clean_url, page_number=len(images) + 1))

        return images
