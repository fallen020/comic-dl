"""WEBTOON series and episode scraper."""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import parse_qs, urljoin, urlparse

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
from ...utils import WEBTOON_PATTERN
from ..base import BaseScraper, _attr_text, meta_get, meta_index, no_images_error
from ..registry import register_scraper

WEBTOON_DOMAIN = "www.webtoons.com"

# Safety cap on how many paginated list pages a series fetch may walk. A real
# WEBTOON series stays well under this; the bound only guards against a site
# that keeps returning pages indefinitely.
_MAX_PAGES = 500

PATTERN = WEBTOON_PATTERN

_SERIES_TITLE_SEL = 'a.subj[href*="list?title_no"]'
_CHAPTER_TITLE_SEL = "h1.subj_episode"
_EPISODE_ITEM_SEL = "li._episodeItem a[href]"
_IMAGE_SEL = "img._images[data-url]"
_NEXT_DATA_SEL = 'script#__NEXT_DATA__[type="application/json"]'
_IMGS_SCR_SEL = "img[src]"

_EP_TITLE_RE = re.compile(r"^(?:Ep|Episode|Ch|Chapter)\.?\s*(\d+)(.*)$", re.IGNORECASE)

_WEBTOON_GENRE_MAP: dict[str, str] = {
    "g_romance": "Romance",
    "g_action": "Action",
    "g_comedy": "Comedy",
    "g_drama": "Drama",
    "g_fantasy": "Fantasy",
    "g_horror": "Horror",
    "g_mystery": "Mystery",
    "g_slice_of_life": "Slice of Life",
    "g_thriller": "Thriller",
    "g_sports": "Sports",
    "g_sci-fi": "Sci-Fi",
    "g_school_life": "School Life",
    "g_supernatural": "Supernatural",
    "g_historical": "Historical",
    "g_romance_fantasy": "Romance Fantasy",
    "g_superhero": "Superhero",
    "g_web_novel": "Web Novel",
    "g_canvas": "Canvas",
    "g_tip": "Tip",
    "g_info": "Info",
    "g_heartwarming": "Heartwarming",
    "g_lgbtq": "LGBTQ+",
}

_BADGE_NOISE_RE = re.compile(
    r"(?:"
    r"[\s\-\u2665\u2764\u2605\u2606\u2661\u25C6\u25C7]*"
    r"(?:UP|NEW|HOT|BEST|like|BGM)"
    r"[\s\-\u2665\u2764\u2605\u2606\u2661\u25C6\u25C7]*"
    r")|(?:[\s\-\u2665\u2764\u2605\u2606\u2661\u25C6\u25C7]+)$",
    re.IGNORECASE,
)


def _parse_url(url: str) -> dict | None:
    m = PATTERN.match(url)
    if m:
        return {
            "lang": m.group(1),
            "category": m.group(2),
            "series_slug": m.group(3),
            "ep_slug": m.group(4),
            "action": m.group(5),
            "title_no": m.group(6),
            "episode_no": m.group(7),
        }

    # Lenient fallback: real WEBTOON URLs often carry extra query parameters
    # (utm_*, platform redirects, ...) that the strict PATTERN rejects, or are
    # missing optional path segments. Parse the query string instead and ignore
    # any unrelated parameters.
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host != WEBTOON_DOMAIN and not host.endswith(f".{WEBTOON_DOMAIN}"):
        return None

    query = parse_qs(parsed.query)
    title_no_raw = query.get("title_no")
    if not title_no_raw or not title_no_raw[0].isdigit():
        return None

    segments = [s for s in parsed.path.split("/") if s]
    if not segments:
        return None

    action: str | None = None
    if segments[-1] in ("list", "viewer"):
        action = segments[-1]
        segments = segments[:-1]

    ep_slug: str | None = None
    if action == "viewer" and segments:
        ep_slug = segments[-1]
        segments = segments[:-1]

    if not action:
        return None

    episode_no_raw = query.get("episode_no")
    episode_no: str | None = None
    if episode_no_raw and episode_no_raw[0].isdigit():
        episode_no = episode_no_raw[0]
    if action == "viewer" and not episode_no:
        return None

    lang = segments[0] if segments else None
    category = segments[1] if len(segments) > 1 else None
    series_slug = segments[2] if len(segments) > 2 else None

    return {
        "lang": lang,
        "category": category,
        "series_slug": series_slug,
        "ep_slug": ep_slug,
        "action": action,
        "title_no": title_no_raw[0],
        "episode_no": episode_no,
    }


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a series page for this source."""
    info = _parse_url(url)
    return info is not None and info["action"] == "list"


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a chapter/gallery page for this source."""
    info = _parse_url(url)
    return info is not None and info["action"] == "viewer"


def normalize_webtoon_url(url: str) -> str:
    """Rewrite a WEBTOON URL into its canonical scheme-less, slug-stripped form."""
    info = _parse_url(url)
    if not info:
        return url
    lang = info["lang"]
    category = info["category"]
    series_slug = info["series_slug"]
    ep_slug = info["ep_slug"]
    action = info["action"]
    title_no = info["title_no"]
    episode_no = info["episode_no"]

    if not lang or not category or not series_slug or not action:
        return url

    path = f"/{lang}/{category}/{series_slug}"
    if ep_slug:
        path += f"/{ep_slug}"
    path += f"/{action}"

    query = f"title_no={title_no}"
    if episode_no:
        query += f"&episode_no={episode_no}"

    return f"https://{WEBTOON_DOMAIN}{path}?{query}"


def _strip_trailing_noise(text: str) -> str:
    while True:
        new = _BADGE_NOISE_RE.sub("", text).strip()
        if new == text:
            break
        text = new
    return text


def _normalize_episode_title(title: str) -> str:
    title = title.strip()
    m = _EP_TITLE_RE.match(title)
    if m:
        num = m.group(1)
        rest = m.group(2).strip().lstrip("-").strip()
        rest = _strip_trailing_noise(rest).rstrip("-").strip()
        if rest:
            return f"Ep. {num} - {rest}"
        return f"Ep. {num}"
    return title


def _extract_authors(
    soup: BeautifulSoup, idx: dict[str, list[str]] | None = None,
) -> list[str]:
    idx = idx if idx is not None else meta_index(soup)
    for key in (
        "prop:com-linewebtoon:webtoon:author",
        "prop:com-linewebtoon:episode:author",
    ):
        vals = idx.get(key)
        if vals and vals[0]:
            return [a.strip() for a in re.split(r"[/,]+\s*", vals[0])]
    return []


def _extract_artists(
    soup: BeautifulSoup, idx: dict[str, list[str]] | None = None,
) -> list[str]:
    """Artists are credited separately (e.g. "Art by X") from the writer(s)."""
    idx = idx if idx is not None else meta_index(soup)
    for key in (
        "prop:com-linewebtoon:webtoon:artist",
        "prop:com-linewebtoon:episode:artist",
    ):
        vals = idx.get(key)
        if vals and vals[0]:
            return [a.strip() for a in re.split(r"[/,]+\s*", vals[0])]
    return []


def _extract_genre(
    soup: BeautifulSoup, idx: dict[str, list[str]] | None = None,
) -> str | None:
    idx = idx if idx is not None else meta_index(soup)
    h2 = soup.select_one("h2.genre")
    if h2:
        for cls in (h2.get("class") or []):
            if not isinstance(cls, str):
                continue
            mapped = _WEBTOON_GENRE_MAP.get(cls)
            if mapped:
                return mapped
        text = h2.get_text(" ", strip=True).strip()
        if text:
            known_genres = {v.lower(): v for v in _WEBTOON_GENRE_MAP.values()}
            for word in re.split(r"[\s/]+", text):
                if word.lower() in known_genres:
                    return known_genres[word.lower()]
    keywords = idx.get("name:keywords")
    if keywords:
        # Compare alphanumerically (strip spaces/hyphens) so live keywords like
        # "Super-hero" match map keys like "superhero".
        def _alnum(text: str) -> str:
            return "".join(ch for ch in text if ch.isalnum())

        known_genres = {_alnum(v.lower()): v for v in _WEBTOON_GENRE_MAP.values()}
        for kw in keywords[0].split(","):
            kw_norm = _alnum(kw.strip().lower())
            if kw_norm in known_genres:
                return known_genres[kw_norm]
    return None


@register_scraper(domain="webtoons.com", capabilities={"chapter", "series"})
class WebtoonScraper(BaseScraper):
    """WEBTOON chapter and series scraper."""

    domain = WEBTOON_DOMAIN
    name = "webtoons"

    def matches_url(self, url: str) -> bool:
        return is_chapter_url(url) or is_series_url(url)

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def scrape_chapter(self, url: str, client: AsyncSession) -> PostMetadata:
        return await self.scrape(url, client)

    async def scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        return await self._scrape_series(url, client)

    async def _scrape_chapter(
        self, url: str, client: AsyncSession,
    ) -> ScrapedChapter:
        parsed = _parse_url(url)
        if not parsed:
            raise ScrapeError(
                f"Invalid WEBTOON URL: {url}",
                hint="Expected a series or episode URL on www.webtoons.com "
                     "with a title_no= parameter.",
            )

        soup = await self.fetch_html(url, client)
        idx = meta_index(soup)

        chapter_title = self._chapter_title(soup, idx)
        series_title = self._series_title(soup, idx)
        description = meta_get(idx, "og:description", "description", "twitter:description")
        cover_url = meta_get(idx, "og:image", "twitter:image")
        authors = _extract_authors(soup, idx)
        artists = _extract_artists(soup, idx)
        if not artists:
            # WEBTOON pages only credit the creator (writer); without a separate
            # artist credit the creator is also the artist.
            artists = authors
        genre_name = _extract_genre(soup, idx)
        genres = [genre_name] if genre_name else []

        images = self._images_from_json(soup)
        if not images:
            images = self._images_from_data_urls(soup)
        if not images:
            images = self._images_from_src(soup)

        if not images:
            raise no_images_error(
                "The chapter may require authentication or be unavailable."
            )

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=series_title or "",
                chapter_title=chapter_title or f"Episode {parsed['episode_no']}",
                chapter_number=parsed.get("episode_no"),
                description=description,
                total_pages=len(images),
                authors=authors,
                artists=artists,
                genres=genres,
                language=parsed["lang"],
                reading_direction="ltr",
            ),
            source=SourceInfo(
                url=url,
                service="webtoons.com",
                user_id=str(parsed["title_no"]),
                post_id=str(parsed.get("episode_no", "")),
            ),
            images=images,
            cover_url=cover_url,
        )

    async def _scrape_series(
        self, url: str, client: AsyncSession,
    ) -> SeriesMetadata:
        parsed = _parse_url(url)
        if not parsed:
            raise ScrapeError(
                f"Invalid WEBTOON URL: {url}",
                hint="Expected a series or episode URL on www.webtoons.com "
                     "with a title_no= parameter.",
            )

        soup = await self.fetch_html(url, client)
        idx = meta_index(soup)

        series_title = (
            self._series_title(soup, idx)
            or parsed["series_slug"].replace("-", " ").title()
        )
        description = meta_get(idx, "og:description", "description", "twitter:description")
        cover_url = meta_get(idx, "og:image", "twitter:image")

        chapters = self._chapters_from_json(soup, url)
        if not chapters:
            chapters = self._chapters_from_episode_items(soup, url)
        if not chapters:
            chapters = self._chapters_from_fallback_links(soup, url)

        if chapters:
            # Numbered pagination links bound the initial probe window, but
            # the widget only shows a few pages around the current one — a long
            # series spans more pages than the first page's links reveal.
            # Fetch the visible window in parallel, then walk one page at a
            # time until a page returns no new chapters.
            seen = set(ch["episode_no"] for ch in chapters if ch["episode_no"])
            max_page = 1
            for a in soup.select('a.pg_page[href*="page="]'):
                qs = parse_qs(urlparse(_attr_text(a.get("href"))).query)
                pn = qs.get("page", [None])[0]
                if pn and pn.isdigit():
                    max_page = max(max_page, int(pn))

            sem = asyncio.Semaphore(3)

            async def _fetch_page(pn: int) -> list[dict]:
                page_url = f"{url}&page={pn}"
                async with sem:
                    try:
                        ps = await self.fetch_html(page_url, client)
                        fresh = [
                            ch for ch in self._chapters_from_episode_items(ps, url)
                            if ch["episode_no"] and ch["episode_no"] not in seen
                        ]
                    except Exception:
                        return []
                for ch in fresh:
                    seen.add(ch["episode_no"] or "")
                return fresh

            results = await asyncio.gather(
                *[_fetch_page(p) for p in range(2, max_page + 1)]
            )
            for batch in results:
                chapters.extend(batch)

            page = max_page + 1
            while page <= _MAX_PAGES:
                batch = await _fetch_page(page)
                if not batch:
                    break
                chapters.extend(batch)
                page += 1

            chapters.sort(
                key=lambda c: int(c["episode_no"]) if c["episode_no"].isdigit() else 0
            )

        if not chapters:
            raise ScrapeError(
                "No chapters found on WEBTOON series page.",
                hint="The series may require authentication.",
            )

        return SeriesMetadata(
            series_title=series_title,
            description=description,
            cover_url=cover_url,
            title_no=parsed["title_no"],
            chapters=chapters,
        )

    @staticmethod
    def _chapter_title(
        soup: BeautifulSoup, idx: dict[str, list[str]] | None = None,
    ) -> str:
        el = soup.select_one(_CHAPTER_TITLE_SEL)
        if el:
            return el.get_text(strip=True)

        if idx is not None:
            title = meta_get(idx, "og:title", "twitter:title")
        else:
            title = WebtoonScraper.meta(soup, "og:title", "twitter:title")
        if title:
            parts = title.rsplit(" - ", 1)
            return parts[-1].strip() if len(parts) == 2 else title

        title_tag = soup.select_one("title")
        if title_tag and title_tag.string:
            text = title_tag.string.strip()
            parts = text.rsplit(" | ", 1)
            return parts[0].strip()

        return ""

    @staticmethod
    def _series_title(
        soup: BeautifulSoup, idx: dict[str, list[str]] | None = None,
    ) -> str:
        el = soup.select_one(_SERIES_TITLE_SEL)
        if el:
            return el.get_text(strip=True)

        if idx is not None:
            title = meta_get(idx, "og:title", "twitter:title")
        else:
            title = WebtoonScraper.meta(soup, "og:title", "twitter:title")
        if title:
            parts = title.rsplit(" - ", 1)
            return parts[0].strip() if len(parts) == 2 else title

        title_tag = soup.select_one("title")
        if title_tag and title_tag.string:
            text = title_tag.string.strip()
            parts = text.rsplit(" | ", 1)
            return parts[-1].strip() if len(parts) == 2 else text

        return ""

    @staticmethod
    def _find_json_script(soup: BeautifulSoup) -> dict | None:
        script = soup.select_one(_NEXT_DATA_SEL)
        if script and script.string:
            try:
                return json.loads(script.string)
            except json.JSONDecodeError:
                pass

        for s in soup.find_all("script"):
            if s.string and "window.__INITIAL_STATE__" in s.string:
                start = s.string.find("{")
                if start == -1:
                    continue
                depth = 0
                for end in range(start, len(s.string)):
                    ch = s.string[end]
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            try:
                                return json.loads(s.string[start:end + 1])
                            except json.JSONDecodeError:
                                pass
                            break
        return None

    @staticmethod
    def _find_image_list(data: dict) -> list[dict] | None:
        images = data.get("props", {}).get("pageProps", {}).get("episode", {}).get("images")
        if images:
            return images
        episode = data.get("episode")
        if episode:
            imgs = episode.get("images")
            if imgs:
                return imgs
            img_list = episode.get("imageList")
            if img_list:
                return [{"url": u} for u in img_list]
        return None

    @staticmethod
    def _images_from_json(soup: BeautifulSoup) -> list[ImageItem] | None:
        data = WebtoonScraper._find_json_script(soup)
        if not data:
            return None
        image_list = WebtoonScraper._find_image_list(data)
        if not image_list:
            return None

        images: list[ImageItem] = []
        seen: set[str] = set()
        for idx, entry in enumerate(image_list, start=1):
            url = entry.get("url", "") if isinstance(entry, dict) else str(entry)
            if not url or url in seen:
                continue
            seen.add(url)
            images.append(ImageItem(url=url, page_number=idx))
        return images or None

    @staticmethod
    def _images_from_data_urls(soup: BeautifulSoup) -> list[ImageItem]:
        images: list[ImageItem] = []
        seen: set[str] = set()
        for img in soup.select(_IMAGE_SEL):
            data_url = _attr_text(img.get("data-url"))
            if not data_url or data_url in seen:
                continue
            if "webtoon-phinf" not in data_url:
                continue
            seen.add(data_url)
            images.append(ImageItem(url=data_url, page_number=len(images) + 1))
        return images

    @staticmethod
    def _images_from_src(soup: BeautifulSoup) -> list[ImageItem]:
        images: list[ImageItem] = []
        seen: set[str] = set()
        for img in soup.select(_IMGS_SCR_SEL):
            img_url = _attr_text(img.get("src"))
            if not img_url or img_url in seen:
                continue
            if "webtoon-phinf" not in img_url:
                continue
            seen.add(img_url)
            images.append(ImageItem(url=img_url, page_number=len(images) + 1))
        return images

    @staticmethod
    def _chapters_from_json(soup: BeautifulSoup, url: str) -> list[dict] | None:
        data = WebtoonScraper._find_json_script(soup)
        if not data:
            return None
        episodes = data.get("props", {}).get("pageProps", {}).get("episodes")
        if not episodes:
            return None

        parsed = _parse_url(url)
        chapters: list[dict] = []
        seen_ep_nos: set[str] = set()
        for ep in episodes:
            ep_no = str(ep.get("episodeNo", ""))
            if not ep_no or ep_no in seen_ep_nos:
                continue
            seen_ep_nos.add(ep_no)
            ep_url = ep.get("url", "")
            if ep_url:
                ep_url = urljoin(url, ep_url)
                if not is_chapter_url(ep_url) and parsed:
                    ep_url = normalize_webtoon_url(
                        f"https://{WEBTOON_DOMAIN}/{parsed['lang']}/{parsed['category']}/{parsed['series_slug']}/ep-{ep_no}/viewer?title_no={parsed['title_no']}&episode_no={ep_no}"
                    )
            chapters.append({
                "title": _normalize_episode_title(ep.get("title", f"Episode {ep_no}")),
                "url": normalize_webtoon_url(ep_url) if ep_url else "",
                "episode_no": ep_no,
            })
        return chapters or None

    @staticmethod
    def _chapters_from_episode_items(soup: BeautifulSoup, url: str) -> list[dict]:
        chapters: list[dict] = []
        seen_ep_nos: set[str] = set()

        for a in soup.select(_EPISODE_ITEM_SEL):
            href = _attr_text(a.get("href"))
            qs = parse_qs(urlparse(href).query)
            ep_no = (qs.get("episode_no") or [""])[0]
            if not ep_no or ep_no in seen_ep_nos:
                continue
            seen_ep_nos.add(ep_no)

            title_el = a.select_one("span.subj")
            ep_title = (
                title_el.get_text(" ", strip=True)
                if title_el
                else a.get_text(" ", strip=True)
            )
            chapters.append({
                "title": _normalize_episode_title(ep_title),
                "url": normalize_webtoon_url(urljoin(url, href)),
                "episode_no": ep_no,
            })
        return chapters

    @staticmethod
    def _chapters_from_fallback_links(soup: BeautifulSoup, url: str) -> list[dict]:
        chapters: list[dict] = []
        seen_ep_nos: set[str] = set()

        list_ul = soup.select_one("#_listUl")
        container = list_ul if list_ul else soup
        for a in container.select('li._episodeItem a[href*="episode_no"]'):
            href = _attr_text(a.get("href"))
            qs = parse_qs(urlparse(href).query)
            ep_no = (qs.get("episode_no") or [""])[0]
            if not ep_no or ep_no in seen_ep_nos:
                continue
            seen_ep_nos.add(ep_no)

            title_el = a.select_one("span.subj")
            ep_title = (
                title_el.get_text(" ", strip=True)
                if title_el
                else a.get_text(" ", strip=True)
            )
            chapters.append({
                "title": _normalize_episode_title(ep_title),
                "url": normalize_webtoon_url(urljoin(url, href)),
                "episode_no": ep_no,
            })
        return chapters


async def scrape_chapter(url: str, client: AsyncSession) -> PostMetadata:
    """Scrape a single WEBTOON episode (test helper)."""
    return await WebtoonScraper().scrape(url, client)


async def scrape_series(url: str, client: AsyncSession) -> SeriesMetadata:
    """Scrape a whole WEBTOON series (test helper)."""
    return await WebtoonScraper().scrape_series(url, client)


_extract_chapter_title = WebtoonScraper._chapter_title
_extract_chapters_from_json = WebtoonScraper._chapters_from_json
_extract_images_from_json = WebtoonScraper._images_from_json
_extract_series_title = WebtoonScraper._series_title
_find_image_list = WebtoonScraper._find_image_list
_find_json_script = WebtoonScraper._find_json_script
