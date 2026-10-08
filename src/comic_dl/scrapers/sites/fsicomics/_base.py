"""Shared FSI Comics scraper: apex host plus its four language mirrors.

FSI runs one Foxiz/WordPress install per language behind the apex host, each a
translation of the same catalogue — apex English plus ``es``/``de``/``fr``/``it``
sub-domains. Slugs, theme markup and taxonomy shape are shared; everything worth
branching on is either localized (title format, reserved path segments, chapter
words) or fixed per host (language, base URL). The per-host subclasses in this
package set those and reuse every rule here.
"""

from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from ....models import (
    ChapterInfo,
    ImageItem,
    PostMetadata,
    ScrapedChapter,
    SeriesMetadata,
    SourceInfo,
    chapter_to_post_metadata,
)
from ....ui import trace
from ....utils import sanitize_filename
from ...base import (
    BaseScraper,
    _attr_text,
    listing_page_error,
    meta_get,
    meta_index,
    no_chapters_error,
    no_images_error,
)

DOMAIN = "fsicomics.com"
BASE = "https://fsicomics.com"

#: Every FSI host. A mirror is reachable only through its own registration —
#: the CLI resolves a source by exact host, so ``es.fsicomics.com`` never falls
#: through to the apex entry.
FSI_HOSTS = frozenset(
    {
        DOMAIN,
        "es.fsicomics.com",
        "de.fsicomics.com",
        "fr.fsicomics.com",
        "it.fsicomics.com",
    }
)

# WordPress machinery: never a comic permalink, and never a taxonomy segment.
_WP_SEGMENTS = frozenset(
    {
        "wp-json",
        "wp-content",
        "wp-admin",
        "wp-login.php",
        "xmlrpc.php",
        "feed",
        "page",
        "comments",
        "search",
    }
)

# Apex taxonomy root plus the mirrors' retired section pages: never a comic
# permalink on the apex.
_APEX_STATIC_SEGMENTS = frozenset(
    {
        "all-porn-comics",
        "porn-comics-video",
        "ai-generated",
        "contact-us",
        "privacy-policy",
        "terms",
        "about",
    }
)

# Legal pages and category roots, localized per mirror. Neither is ever a comic
# permalink — but a category root *is* the first segment of a mirror's taxonomy
# URL (``/{category}/{artist}/``), so only chapter URLs reject these outright.
_MIRROR_STATIC_SEGMENTS = frozenset(
    {
        # 2257 notice, DMCA and legal pages.
        "18-u-s-c-%c2%a7-2257",
        "contactenos",
        "politica-de-privacidad",
        "descargo-de-responsabilidad",
        "terminos-de-servicio",
        "dmca",
        "datenschutzerklarung",
        "haftungsausschluss",
        "kontaktieren-sie-uns",
        "nutzungsbedingungen",
        "conditions-dutilisation",
        "contactez-nous",
        "politique-de-confidentialite",
        "politique-dmca",
        "contattaci",
        "disclaimer",
        "informativa-sulla-privacita",
        "termini-di-servizio",
        # Top-level category roots.
        "comics-porno-3d",
        "comics-porno-occidental",
        "comics-porno-occidentaux",
        "comics-vip",
        "ultimos-comics-porno",
        "manga-hentai",
        "manga",
        "hentai-manga",
        "doujinshi",
        "3d-porno-comics",
        "westliche-porno-comics",
        "fumetti-porno-3d",
        "fumetti-porno-occidentali",
    }
)

_RESERVED_SEGMENTS = _WP_SEGMENTS | _APEX_STATIC_SEGMENTS | _MIRROR_STATIC_SEGMENTS

# A chapter permalink is a single path segment on every host. Series pages
# differ: the apex nests its taxonomy under ``/all-porn-comics/``, the mirrors
# expose it at ``/{category}/{artist-or-group}/`` with no fixed root.
_APEX_TAXONOMY_ROOT = "all-porn-comics"

_WORDPRESS_RESIZE_RE = re.compile(r"-\d+x\d+(?=\.\w+$)")
_MAX_SERIES_PAGES = 50

# "Chapter"/"Ch." and each mirror's own word: Capítulo, Capitolo, Chapitre,
# Kapitel. ``\s`` also matches the non-breaking space the mirrors put before the
# number ("Capítulo\u00a03"). Longer alternatives must precede the bare ``ch``.
_CHAPTER_NUMBER_RE = re.compile(
    r"(?:cap[ií]tulo|capitolo|chapitre|kapitel|chapter|ch)[.\s]*#?\s*(\d+)",
    re.IGNORECASE,
)

# The same vocabulary in slugs, plus the epilogue forms ("-epilogo-") that carry
# no number. Marking the split point is what groups a chapter into its series.
_CHAPTER_MARKER_RE = re.compile(
    r"-(?:cap[ií]tulo|capitolo|chapitre|kapitel|chapter|epilogo|epilog)-",
    re.IGNORECASE,
)

_CHAPTER_NUM_PREFIX_RE = re.compile(r"^\d+[\s._-]*")

# Titles join <chapter> / <artist> / <site> with either an ASCII hyphen or an
# en dash, and the site tail is localized — "FSIComics" on the apex,
# "FSI Comics {ES,Italiano,Français}" on the mirrors.
_TITLE_SEPARATOR_RE = re.compile(r"\s+[-\u2013]\s+")
_SITE_TAIL_RE = re.compile(r"^fsi\s*comics\b", re.IGNORECASE)

# The SEO filler paragraph is translated per mirror, so the markers must be too.
_BOILERPLATE_MARKERS = (
    # Apex.
    "is the publisher of this comic book episode",
    "watch comics feature genres such as",
    "join the new channel for latest comics",
    "join our telegram channel",
    "support the artist from",
    # es.
    "es el editor de este episodio de c\u00f3mic",
    "disfruta de c\u00f3mics con g\u00e9neros como",
    "\u00fanete al nuevo canal",
    "nuestro canal de telegram",
    # de.
    "ist der herausgeber dieser folge",
    "entdecke comic-genres wie",
    "treten sie dem neuen kanal",
    "unserem telegram-kanal",
    # fr.
    "auteur de cet",
    "rejoignez le nouveau canal",
    "notre canal telegram",
    "statut :",
    # it.
    "editore di questo episodio",
    "scopri fumetti che spaziano tra generi come",
    "unisciti al nuovo canale",
    "nostro canale telegram",
)

# Chapter cards are the taxonomy grid's title anchors. The heading level depends
# on the page shape: ``h4`` in ``.p-wrap p-grid p-grid-1`` on the apex taxonomy,
# ``h3`` in the same grid on the mirrors. The theme also reuses ``.entry-title``
# for *other* comics in two places that must stay out — the cross-promo carousel
# (``swiper`` ancestors, excluded by class) and a sidebar widget that renders
# ``h5`` in ``.p-wrap p-small p-list-small-2``, excluded by never matching
# ``h5``.
_SERIES_CARD_SELECTOR = ".p-wrap h3.entry-title a[href], .p-wrap h4.entry-title a[href]"


def _fsi_host(url: str) -> str | None:
    """The FSI host in ``url`` (``www.`` folded), or ``None`` when foreign."""
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host if host in FSI_HOSTS else None


def _path_segments(url: str) -> list[str]:
    return [seg for seg in urlparse(url).path.split("/") if seg]


def _is_comic_slug(segment: str) -> bool:
    return not segment.startswith("wp-") and segment not in _RESERVED_SEGMENTS


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a chapter/gallery page for this source."""
    if _fsi_host(url) is None:
        return False
    segments = _path_segments(url)
    return len(segments) == 1 and _is_comic_slug(segments[0])


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a series/listing page for this source."""
    host = _fsi_host(url)
    if host is None:
        return False
    segments = _path_segments(url)
    if host == DOMAIN:
        return len(segments) >= 2 and segments[0] == _APEX_TAXONOMY_ROOT
    # A mirror's taxonomy is exactly /{category}/{artist-or-group}/: the
    # category root is a reserved word, so only machinery is rejected here.
    if len(segments) != 2:
        return False
    head, tail = segments
    if head in _WP_SEGMENTS or head.startswith("wp-"):
        return False
    return _is_comic_slug(tail)


def _derive_series_title(url: str, chapter_title: str) -> str:
    """Guess the series directory for a chapter URL.

    ``family-debt-chapter-1-traplust`` with title ``Family Debt Chapter 1``
    (and a ``- TRAPLust`` artist tail) yields ``Family Debt - TRAPLust``.
    Artist casing is taken from the title when it matches the slug's artist.
    Returns ``""`` when the slug has no chapter marker, so callers keep
    the page metadata as-is.
    """
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    parts = _CHAPTER_MARKER_RE.split(slug, maxsplit=1)
    if len(parts) < 2:
        return ""
    series = parts[0].replace("-", " ").strip().title()

    artist = _CHAPTER_NUM_PREFIX_RE.sub("", parts[1]).replace("-", " ").strip().title()
    # The artist segment, with its original casing, is the last title part once
    # the localized site tail is dropped. The slug only carries an ASCII-folded
    # name ("emmas-corruption" for "Emma's Corruption"), so the title wins when
    # both name the same artist.
    title_parts = _title_parts(chapter_title)
    title_artist = title_parts[-1] if len(title_parts) >= 2 else ""
    if title_artist and (not artist or title_artist.lower() == artist.lower()):
        artist = title_artist

    if not artist:
        return series
    return f"{series} - {artist}"


def _title_parts(page_title: str) -> list[str]:
    """Split a page title into ``[chapter, artist, ...]``, dropping the site tail.

    Separators are an ASCII hyphen or an en dash depending on the mirror, and
    the tail is the localized site name. Matching the tail by shape rather than
    against ``og:site_name`` is what keeps every host working: ``og:site_name``
    is a long marketing string on all five and never equals the title segment.
    """
    parts = [p.strip() for p in _TITLE_SEPARATOR_RE.split(page_title) if p.strip()]
    if len(parts) > 1 and _SITE_TAIL_RE.match(parts[-1]):
        return parts[:-1]
    return parts


# WordPress archive/taxonomy pages carry these exact body classes. A single
# comic post never does — it has `single` and `postid-<n>` plus prefixed
# classes like `category-<slug>`/`tag-<slug>`, which won't match because the
# set is checked as exact whitespace-delimited tokens.
_ARCHIVE_BODY_CLASSES = frozenset(
    {
        "archive",
        "category",
        "tag",
        "tax",
        "term",
        "search",
        "error404",
    }
)


def _is_archive_page(soup: BeautifulSoup) -> bool:
    """True when the page is a category/tag/archive listing, not a comic."""
    body = soup.select_one("body")
    if body is None:
        return False
    classes = {str(c) for c in (body.get("class") or []) if isinstance(c, str)}
    return bool(classes & _ARCHIVE_BODY_CLASSES)


def _clean_image_url(raw: str) -> str:
    clean = raw.split("?")[0].split("#")[0]
    clean = _WORDPRESS_RESIZE_RE.sub("", clean)
    return clean


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

    parts = _title_parts(page_title)

    # The artist is the last content segment, not the second one: the
    # Italian mirror splices a translated series title in between
    # ("Oba to Haha Zenpen - Zia e Madre Capitolo 2 <en dash> Nishikawa Kou").
    if len(parts) >= 2:
        chapter_title = parts[0]
        series_title = parts[-1]
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
    body = _extract_body_description(soup)
    if body:
        return body
    idx = idx if idx is not None else meta_index(soup)
    return meta_get(idx, "og:description", "twitter:description", "description")


def _extract_body_description(soup: BeautifulSoup) -> str:
    """Full summary from the post body, not the truncated meta tags.

    RankMath caps ``og:description`` mid-sentence, while ``.entry-content``
    holds the complete text. SEO filler paragraphs (publisher boast, genre
    keyword dump, Telegram promo, status widget) are dropped in every language;
    genuine notes (e.g. the split-chapter notice) are kept.
    """
    entry = soup.select_one(".entry-content")
    if entry is None:
        return ""
    paragraphs = []
    for p in entry.find_all("p"):
        text = p.get_text(" ", strip=True)
        if not text:
            continue
        if any(m in text.lower() for m in _BOILERPLATE_MARKERS):
            continue
        paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _extract_cover(soup: BeautifulSoup, idx: dict[str, list[str]] | None = None) -> str:
    idx = idx if idx is not None else meta_index(soup)
    content = meta_get(idx, "og:image", "twitter:image")
    if content:
        return _clean_image_url(content.split(",")[0].strip())
    return ""


def _extract_chapter_number(title: str) -> str | None:
    m = _CHAPTER_NUMBER_RE.search(title)
    return m.group(1) if m else None


def _in_carousel(tag) -> bool:
    """True when ``tag`` sits inside the cross-promo carousel block."""
    for parent in tag.parents:
        classes = parent.get("class") or []
        if any(c == "post-carousel" or c.startswith("swiper") for c in classes):
            return True
    return False


def _chapter_card_links(soup: BeautifulSoup) -> list:
    """Title anchors of this taxonomy's own chapters (foreign cards out)."""
    return [link for link in soup.select(_SERIES_CARD_SELECTOR) if not _in_carousel(link)]


_POSTID_CLASS_RE = re.compile(r"\bpostid-(\d+)")
_POST_ID_ATTR_RE = re.compile(r'id="post-(\d+)"')


def _extract_post_id(soup: BeautifulSoup, raw_html: str | None = None) -> str:
    body = soup.select_one("body")
    if body is not None:
        m = _POSTID_CLASS_RE.search(" ".join(body.get("class") or []))
        if m:
            return m.group(1)
    html = raw_html if raw_html is not None else str(soup)
    m = _POST_ID_ATTR_RE.search(html)
    return m.group(1) if m else ""


def _extract_artists(soup: BeautifulSoup, idx: dict[str, list[str]] | None = None) -> list[str]:
    idx = idx if idx is not None else meta_index(soup)
    author = meta_get(idx, "author")
    if author:
        return [author]
    title_tag = soup.select_one("title")
    page_title = unescape(title_tag.get_text(strip=True)) if title_tag else ""
    if not page_title:
        return []

    parts = _title_parts(page_title)
    if len(parts) >= 2:
        return [parts[-1]]
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
    the site name ("FSI Comics"). On the mirrors ``article:section`` sometimes
    carries a translated category name instead, so treat it as best-effort.
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
                        "Article",
                        "NewsArticle",
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


async def _collect_series_pages(
    url: str, soup: BeautifulSoup, client: AsyncSession
) -> list[tuple[str, BeautifulSoup | None]]:
    """Walk series pagination, tolerating isolated dead pages.

    Page URLs form a predictable ``/page/N/`` chain, but only a live page
    reveals whether a next one exists — hence the serial walk. A single dead
    page (transient blip) is probed past instead of truncating the listing;
    only consecutive misses end it, so a genuinely finished series still
    stops after two wasted fetches.
    """
    pages: list[tuple[str, BeautifulSoup | None]] = [(url, soup)]

    async def fetch_page(u: str) -> tuple[str, BeautifulSoup | None]:
        try:
            pr = await BaseScraper._timeout_get(u, client)
            pr.raise_for_status()
            return u, BeautifulSoup(pr.text, "lxml")
        # One dead page must not fail the fan-out.
        except Exception:  # nosec
            return u, None

    dead_streak = 0
    page_num = 2
    has_next = soup.select_one("a.next.page-numbers") is not None
    while has_next and page_num <= _MAX_SERIES_PAGES:
        page_url = f"{url.rstrip('/')}/page/{page_num}/"
        u, ps = await fetch_page(page_url)
        pages.append((u, ps))
        if ps is None:
            dead_streak += 1
            if dead_streak >= 2:
                break
            trace(f"fsicomics: series page {page_num} unreadable, probing on")
        else:
            dead_streak = 0
            has_next = ps.select_one("a.next.page-numbers") is not None
        page_num += 1
    return pages


class FsicomixScraper(BaseScraper):
    """FSI Comics chapter and series scraper, shared by all five hosts.

    Subclasses set ``domain``/``name``/``site_id``/``display_name``/``language``
    /``base`` and register themselves; every extraction rule lives here.
    """

    domain = DOMAIN
    language = "en"
    base = BASE

    def _owns(self, url: str) -> bool:
        """Restrict this entry to its own host.

        ``registry.source_for_url`` walks every registered entry in domain
        order, so without the host check the apex entry would claim the
        mirrors' URLs.
        """
        return _fsi_host(url) == self.domain

    def matches_url(self, url: str) -> bool:
        return self._owns(url) and (is_chapter_url(url) or is_series_url(url))

    def matches_series_url(self, url: str) -> bool:
        return self._owns(url) and is_series_url(url)

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        chapter = await self._scrape_chapter(url, client)
        return chapter_to_post_metadata(chapter)

    async def scrape_series(self, url: str, client: AsyncSession) -> SeriesMetadata:
        return await self._scrape_series(url, client)

    async def _scrape_chapter(
        self,
        url: str,
        client: AsyncSession,
    ) -> ScrapedChapter:
        soup, html = await self.fetch_html_raw(url, client)
        idx = meta_index(soup)

        if _is_archive_page(soup):
            raise listing_page_error(
                "FSI Comics",
                f"{self.base}/{{comic-slug}}/",
            )

        title_tag = soup.select_one("title")
        full_title = unescape(title_tag.get_text(strip=True)) if title_tag else ""

        series_title, chapter_title = _extract_meta(soup, idx)
        # The split chapter title drops the artist segment and its casing, which
        # the slug-derived series dir wants for "Series - Artist".
        derived_series = _derive_series_title(url, full_title or chapter_title)
        if derived_series:
            series_title = derived_series
        description = _extract_description(soup, idx)
        cover_url = _extract_cover(soup, idx)
        images = _extract_images(soup)
        artists = _extract_artists(soup, idx)
        genres = _extract_genres(soup, idx)
        publisher = _extract_publisher(soup, idx)

        if not chapter_title:
            chapter_title = full_title

        chapter_number = _extract_chapter_number(chapter_title) or _extract_chapter_number(
            full_title
        )
        if chapter_number and not chapter_title.startswith("Chapter"):
            chapter_title = f"Chapter {chapter_number}"

        if not images:
            raise no_images_error()

        return ScrapedChapter(
            info=ChapterInfo(
                series_title=sanitize_filename(series_title) or "Untitled",
                chapter_title=sanitize_filename(chapter_title) or "Chapter",
                chapter_number=chapter_number,
                description=description,
                language=self.language,
                artists=artists,
                genres=genres,
                publisher=publisher,
                reading_direction="ltr",
                total_pages=len(images),
            ),
            source=SourceInfo(url=url, service=self.domain, post_id=_extract_post_id(soup, html)),
            images=images,
            cover_url=cover_url,
        )

    async def _scrape_series(
        self,
        url: str,
        client: AsyncSession,
    ) -> SeriesMetadata:
        # A series URL is a WordPress taxonomy page (``archive category``),
        # so the archive guard used in chapter mode must not fire here. An
        # empty listing raises ``no_chapters_error`` below instead.
        soup = await self.fetch_html(url, client)

        idx = meta_index(soup)

        title_tag = soup.select_one("title")
        page_title = title_tag.get_text(strip=True) if title_tag else ""
        parts = _title_parts(page_title)
        series_title = parts[0] if parts else ""

        description = _extract_description(soup, idx)
        cover_url = _extract_cover(soup, idx)

        title_no = url.rstrip("/").rsplit("/", 1)[-1]

        chapters: list[dict] = []
        seen_urls: set[str] = set()

        pages_to_fetch = await _collect_series_pages(url, soup, client)

        for _page_url, ps in pages_to_fetch:
            if ps is None:
                continue
            for link in _chapter_card_links(ps):
                href = _attr_text(link.get("href"))
                if not href or href in seen_urls:
                    continue
                seen_urls.add(href)
                ch_title = link.get_text(strip=True) or ""
                chapters.append(
                    {
                        "title": ch_title,
                        "url": urljoin(url, href),
                        "episode_no": _extract_chapter_number(ch_title) or str(len(chapters) + 1),
                    }
                )

        if not chapters:
            raise no_chapters_error()

        return SeriesMetadata(
            series_title=series_title or title_no.replace("-", " ").title(),
            description=description,
            cover_url=cover_url,
            title_no=title_no,
            chapters=chapters,
        )
